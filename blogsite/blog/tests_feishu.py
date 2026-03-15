import json
from uuid import uuid4
from unittest.mock import ANY, patch

from asgiref.sync import async_to_sync
from channels.testing.websocket import WebsocketCommunicator
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from blogsite.asgi import application

from .feishu_views import _extract_text_message, _parse_calendar_command, process_feishu_event
from .models import FeishuChatSession, RemoteChangeRequest, TerminalAccessLink
from .remote_agent import (
    INTENT_CHAT,
    INTENT_REPO,
    RemotePlanningError,
    apply_change_plan,
    classify_user_request,
    format_plan_for_user,
)
from .remote_executor import RemoteExecutor, RemoteExecutorError
from .terminal_web import create_terminal_access_code, create_terminal_access_token
from .websocket_security import terminal_origin_validator


class FeishuWebhookTests(TestCase):
    @override_settings(FEISHU_VERIFICATION_TOKEN="verify-123")
    def test_url_verification_returns_challenge(self):
        response = self.client.post(
            reverse("feishu-events"),
            data=json.dumps(
                {
                    "type": "url_verification",
                    "challenge": "abc",
                    "token": "verify-123",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"challenge": "abc"})

    @override_settings(FEISHU_VERIFICATION_TOKEN="verify-123")
    def test_invalid_token_is_rejected(self):
        response = self.client.post(
            reverse("feishu-events"),
            data=json.dumps(
                {
                    "type": "url_verification",
                    "challenge": "abc",
                    "token": "wrong-token",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    @patch("blog.feishu_views.start_event_processing")
    @override_settings(FEISHU_VERIFICATION_TOKEN="verify-123")
    def test_message_event_starts_background_processing(self, start_event_processing_mock):
        payload = {
            "schema": "2.0",
            "header": {
                "event_id": "evt_1",
                "event_type": "im.message.receive_v1",
                "type": "event_callback",
                "token": "verify-123",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_x"},
                },
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": "hello"}, ensure_ascii=False),
                    "mentions": [],
                },
            },
        }

        response = self.client.post(
            reverse("feishu-events"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"code": 0})
        start_event_processing_mock.assert_called_once()


class FeishuParsingTests(SimpleTestCase):
    def test_extract_text_message_removes_leading_mention(self):
        event = {
            "message": {
                "message_type": "text",
                "content": json.dumps({"text": "@linuxclaw 帮我修复 bug"}, ensure_ascii=False),
                "mentions": [{"name": "linuxclaw"}],
            }
        }

        self.assertEqual(_extract_text_message(event), "帮我修复 bug")

    def test_format_plan_for_user_includes_token_and_files(self):
        message = format_plan_for_user(
            {
                "summary": "修复首页 500",
                "reply": "我会先修改视图并补测试。",
                "edits": [
                    {"type": "replace_text", "path": "blog/views.py"},
                    {"type": "replace_text", "path": "blog/tests.py"},
                ],
                "tests": ["python manage.py test blog.tests"],
                "risks": ["需要确认生产环境有同样的配置"],
            },
            "deadbeef",
        )

        self.assertIn("审批码: deadbeef", message)
        self.assertIn("blog/views.py", message)
        self.assertIn("/approve deadbeef", message)

    def test_classify_user_request_respects_chat_prefix(self):
        route = classify_user_request("/chat 你是谁")

        self.assertEqual(route["mode"], INTENT_CHAT)
        self.assertEqual(route["message"], "你是谁")

    def test_classify_user_request_respects_repo_prefix(self):
        route = classify_user_request("/repo 帮我修复首页报错")

        self.assertEqual(route["mode"], INTENT_REPO)
        self.assertEqual(route["message"], "帮我修复首页报错")

    def test_parse_calendar_create_command(self):
        command = _parse_calendar_command("/calendar create 2026-03-15 14:00 15:00 产品评审 | 对齐需求")

        self.assertEqual(command["action"], "create")
        self.assertEqual(command["summary"], "产品评审")
        self.assertEqual(command["description"], "对齐需求")

    def test_parse_calendar_create_command_rejects_invalid_range(self):
        with self.assertRaises(ValueError):
            _parse_calendar_command("/calendar create 2026-03-15 15:00 14:00 产品评审")


class FeishuRoutingTests(TestCase):
    def _payload(self, text):
        return {
            "schema": "2.0",
            "header": {
                "event_id": f"evt_{uuid4().hex}",
                "event_type": "im.message.receive_v1",
                "type": "event_callback",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "ou_x"},
                },
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                    "mentions": [],
                },
            },
        }

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.reply_text")
    @patch("blog.feishu_views.answer_general_question")
    @patch("blog.feishu_views.classify_user_request")
    def test_chat_messages_answer_directly_without_repo_ack(
        self,
        classify_user_request_mock,
        answer_general_question_mock,
        reply_text_mock,
        send_chat_message_mock,
    ):
        classify_user_request_mock.return_value = {
            "mode": "chat",
            "message": "你是谁",
            "reason": "general qa",
        }
        answer_general_question_mock.return_value = "我是 linuxclaw。"

        process_feishu_event(self._payload("你是谁"))

        reply_text_mock.assert_not_called()
        answer_general_question_mock.assert_called_once_with("你是谁", history=[])
        send_chat_message_mock.assert_called_once_with("oc_1", "我是 linuxclaw。")

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.reply_text")
    @patch("blog.feishu_views.build_change_request_plan")
    @patch("blog.feishu_views.classify_user_request")
    def test_repo_messages_ack_before_building_plan(
        self,
        classify_user_request_mock,
        build_change_request_plan_mock,
        reply_text_mock,
        send_chat_message_mock,
    ):
        classify_user_request_mock.return_value = {
            "mode": "repo",
            "message": "修复首页报错",
            "reason": "repo task",
        }
        build_change_request_plan_mock.return_value = {
            "summary": "检查首页错误",
            "reply": "我先整理问题。",
            "edits": [],
            "tests": [],
            "risks": [],
        }

        process_feishu_event(self._payload("修复首页报错"))

        reply_text_mock.assert_called_once()
        send_chat_message_mock.assert_called_once_with("oc_1", "我先整理问题。")

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.reply_text")
    @patch("blog.feishu_views.classify_user_request")
    @patch("blog.feishu_views.create_calendar_event")
    def test_calendar_create_command_does_not_trigger_repo_flow(
        self,
        create_calendar_event_mock,
        classify_user_request_mock,
        reply_text_mock,
        send_chat_message_mock,
    ):
        create_calendar_event_mock.return_value = {
            "event_id": "evt_cal_1",
            "summary": "产品评审",
            "start_time": {"timestamp": "1773554400", "timezone": "Asia/Shanghai"},
            "end_time": {"timestamp": "1773558000", "timezone": "Asia/Shanghai"},
        }

        process_feishu_event(self._payload("/calendar create 2026-03-15 14:00 15:00 产品评审 | 对齐需求"))

        classify_user_request_mock.assert_not_called()
        reply_text_mock.assert_not_called()
        create_calendar_event_mock.assert_called_once()
        message = send_chat_message_mock.call_args.args[1]
        self.assertIn("已创建日程: 产品评审", message)
        self.assertIn("evt_cal_1", message)

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.list_calendar_events")
    def test_calendar_list_command_returns_events(self, list_calendar_events_mock, send_chat_message_mock):
        list_calendar_events_mock.return_value = {
            "calendar_id": "cal_1",
            "items": [
                {
                    "event_id": "evt_1",
                    "summary": "产品评审",
                    "start_time": {"timestamp": "1773554400", "timezone": "Asia/Shanghai"},
                    "end_time": {"timestamp": "1773558000", "timezone": "Asia/Shanghai"},
                }
            ],
        }

        process_feishu_event(self._payload("/calendar list 2026-03-15"))

        list_calendar_events_mock.assert_called_once()
        message = send_chat_message_mock.call_args.args[1]
        self.assertIn("2026-03-15 日程列表", message)
        self.assertIn("产品评审", message)
        self.assertIn("evt_1", message)

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.delete_calendar_event")
    def test_calendar_delete_command_deletes_event(self, delete_calendar_event_mock, send_chat_message_mock):
        process_feishu_event(self._payload("/calendar delete evt_123"))

        delete_calendar_event_mock.assert_called_once_with("evt_123")
        send_chat_message_mock.assert_called_once_with("oc_1", "已删除日程 evt_123。")

    @patch("blog.feishu_views._send_chat_message")
    def test_invalid_calendar_command_returns_help(self, send_chat_message_mock):
        process_feishu_event(self._payload("/calendar foo"))

        message = send_chat_message_mock.call_args.args[1]
        self.assertIn("日程命令", message)

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.classify_user_request")
    @patch("blog.feishu_views.answer_read_only_request")
    def test_read_only_server_query_runs_directly(
        self,
        answer_read_only_request_mock,
        classify_user_request_mock,
        send_chat_message_mock,
    ):
        answer_read_only_request_mock.return_value = "已直接查看服务器磁盘与存储信息。"

        process_feishu_event(self._payload("你帮我查下当前服务器的存储有多大"))

        classify_user_request_mock.assert_not_called()
        answer_read_only_request_mock.assert_called_once()
        send_chat_message_mock.assert_called_once_with("oc_1", "已直接查看服务器磁盘与存储信息。")

    @patch("blog.feishu_views._handle_approve")
    @patch("blog.feishu_views._send_chat_message")
    def test_confirm_execute_uses_session_pending_token(self, send_chat_message_mock, handle_approve_mock):
        preferred = RemoteChangeRequest.objects.create(
            source_message_id="om_plan",
            chat_id="oc_1",
            user_open_id="ou_x",
            prompt="修复首页报错",
            plan={"summary": "修复首页", "edits": [{"type": "replace_text", "path": "blog/views.py"}]},
        )
        RemoteChangeRequest.objects.create(
            source_message_id="om_plan_2",
            chat_id="oc_1",
            user_open_id="ou_x",
            prompt="另一个待处理任务",
            plan={"summary": "修复测试", "edits": [{"type": "replace_text", "path": "blog/tests.py"}]},
        )
        FeishuChatSession.objects.create(
            chat_id="oc_1",
            user_open_id="ou_x",
            last_pending_token=preferred.approval_token,
        )

        process_feishu_event(self._payload("确认执行"))

        handle_approve_mock.assert_called_once_with("oc_1", preferred.approval_token, session=ANY)
        self.assertIn("开始执行最近的审批码", send_chat_message_mock.call_args.args[1])

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.reply_text")
    @patch("blog.feishu_views.apply_change_plan")
    @patch("blog.feishu_views.build_change_request_plan")
    @patch("blog.feishu_views.classify_user_request")
    def test_low_risk_repo_change_executes_immediately(
        self,
        classify_user_request_mock,
        build_change_request_plan_mock,
        apply_change_plan_mock,
        reply_text_mock,
        send_chat_message_mock,
    ):
        classify_user_request_mock.return_value = {
            "mode": "repo",
            "message": "帮我修复首页标题",
            "reason": "repo task",
        }
        build_change_request_plan_mock.return_value = {
            "summary": "修复首页标题",
            "reply": "直接修复首页标题。",
            "edits": [{"type": "replace_text", "path": "blog/views.py"}],
            "tests": ["python manage.py test blog.tests"],
            "risks": [],
        }
        apply_change_plan_mock.return_value = {
            "summary": "修复首页标题",
            "tests": [{"command": "python manage.py test blog.tests", "output": "OK"}],
            "diff": "diff --git a/blog/views.py b/blog/views.py",
            "files": ["blog/views.py"],
        }

        process_feishu_event(self._payload("帮我修复首页标题"))

        reply_text_mock.assert_called_once()
        apply_change_plan_mock.assert_called_once()
        self.assertEqual(RemoteChangeRequest.objects.count(), 0)
        message = send_chat_message_mock.call_args.args[1]
        self.assertIn("执行完成", message)
        session = FeishuChatSession.objects.get(chat_id="oc_1")
        self.assertEqual(session.last_mode, "repo")
        self.assertEqual(session.last_pending_token, "")

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.reply_text")
    @patch("blog.feishu_views.apply_change_plan")
    @patch("blog.feishu_views.build_change_request_plan")
    @patch("blog.feishu_views.classify_user_request")
    def test_high_risk_repo_change_still_requires_approval(
        self,
        classify_user_request_mock,
        build_change_request_plan_mock,
        apply_change_plan_mock,
        reply_text_mock,
        send_chat_message_mock,
    ):
        classify_user_request_mock.return_value = {
            "mode": "repo",
            "message": "帮我改一下部署配置",
            "reason": "repo task",
        }
        build_change_request_plan_mock.return_value = {
            "summary": "修改部署配置",
            "reply": "我先整理改动计划。",
            "edits": [{"type": "replace_text", "path": "blogsite/settings.py"}],
            "tests": [],
            "risks": [],
        }

        process_feishu_event(self._payload("帮我改一下部署配置"))

        reply_text_mock.assert_called_once()
        apply_change_plan_mock.assert_not_called()
        request_obj = RemoteChangeRequest.objects.get()
        message = send_chat_message_mock.call_args.args[1]
        self.assertIn(f"/approve {request_obj.approval_token}", message)
        session = FeishuChatSession.objects.get(chat_id="oc_1")
        self.assertEqual(session.last_pending_token, request_obj.approval_token)

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.answer_general_question")
    @patch("blog.feishu_views.classify_user_request")
    def test_session_history_is_created_and_persisted(
        self,
        classify_user_request_mock,
        answer_general_question_mock,
        send_chat_message_mock,
    ):
        classify_user_request_mock.return_value = {
            "mode": "chat",
            "message": "你是谁",
            "reason": "general qa",
        }
        answer_general_question_mock.return_value = "我是 linuxclaw。"

        process_feishu_event(self._payload("你是谁"))

        session = FeishuChatSession.objects.get(chat_id="oc_1")
        self.assertEqual(session.last_mode, "chat")
        self.assertEqual(
            session.history,
            [
                {"role": "user", "content": "你是谁"},
                {"role": "assistant", "content": "我是 linuxclaw。"},
            ],
        )

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.RemoteTerminalManager")
    @override_settings(APP_PUBLIC_BASE_URL="http://example.com")
    @override_settings(FEISHU_TERMINAL_ENABLED=True, FEISHU_TERMINAL_ALLOWED_OPEN_IDS=["ou_x"])
    def test_term_open_codex_enables_passthrough(self, terminal_manager_cls, send_chat_message_mock):
        terminal_manager = terminal_manager_cls.return_value
        terminal_manager.open.return_value = {
            "created": True,
            "cwd": "/opt/linuxclaw",
            "program": "codex",
            "output": "codex ready",
        }

        process_feishu_event(self._payload("/term open codex"))

        terminal_manager.open.assert_called_once_with("oc_1", profile="codex", cwd="")
        session = FeishuChatSession.objects.get(chat_id="oc_1")
        terminal_state = session.memory["terminal"]
        self.assertTrue(terminal_state["active"])
        self.assertTrue(terminal_state["passthrough"])
        self.assertEqual(terminal_state["profile"], "codex")
        self.assertEqual(terminal_state["program"], "codex")
        message = send_chat_message_mock.call_args.args[1]
        self.assertIn("终端已就绪", message)
        self.assertIn("http://example.com/blog/t/", message)
        self.assertNotIn("/opt/linuxclaw", message)
        self.assertNotIn("codex ready", message)
        self.assertLessEqual(len(message.splitlines()), 2)

    @patch("blog.feishu_views._send_chat_message")
    @patch("blog.feishu_views.classify_user_request")
    @patch("blog.feishu_views.RemoteTerminalManager")
    @override_settings(FEISHU_TERMINAL_ENABLED=True, FEISHU_TERMINAL_ALLOWED_OPEN_IDS=["ou_x"])
    def test_terminal_passthrough_sends_plain_text_to_session(
        self,
        terminal_manager_cls,
        classify_user_request_mock,
        send_chat_message_mock,
    ):
        terminal_manager = terminal_manager_cls.return_value
        terminal_manager.send.return_value = {
            "cwd": "/opt/linuxclaw",
            "program": "bash",
            "output": "old output\npwd\n/opt/linuxclaw",
        }
        FeishuChatSession.objects.create(
            chat_id="oc_1",
            user_open_id="ou_x",
            memory={
                "terminal": {
                    "active": True,
                    "passthrough": True,
                    "profile": "shell",
                    "output": "old output",
                }
            },
        )

        process_feishu_event(self._payload("pwd"))

        terminal_manager.send.assert_called_once_with("oc_1", "pwd", enter=True)
        classify_user_request_mock.assert_not_called()
        message = send_chat_message_mock.call_args.args[1]
        self.assertIn("终端回显", message)
        self.assertIn("/opt/linuxclaw", message)

    @patch("blog.feishu_views._send_chat_message")
    @override_settings(FEISHU_TERMINAL_ENABLED=True, FEISHU_TERMINAL_ALLOWED_OPEN_IDS=["ou_x"])
    def test_term_mode_off_disables_passthrough(self, send_chat_message_mock):
        FeishuChatSession.objects.create(
            chat_id="oc_1",
            user_open_id="ou_x",
            memory={
                "terminal": {
                    "active": True,
                    "passthrough": True,
                    "profile": "shell",
                }
            },
        )

        process_feishu_event(self._payload("/term mode off"))

        session = FeishuChatSession.objects.get(chat_id="oc_1")
        self.assertFalse(session.memory["terminal"]["passthrough"])
        self.assertIn("终端透传已关闭", send_chat_message_mock.call_args.args[1])


class RemoteExecutorValidationTests(SimpleTestCase):
    @override_settings(
        REMOTE_SSH_HOST="example.com",
        REMOTE_SSH_USER="deploy",
        REMOTE_PROJECT_ROOT="/srv/linuxclaw",
        REMOTE_ALLOWED_TEST_PREFIXES=["pytest", "python manage.py test"],
    )
    def test_resolve_path_blocks_escape_from_project_root(self):
        executor = RemoteExecutor()

        with self.assertRaises(RemoteExecutorError):
            executor._resolve_path("../etc/passwd")

    @override_settings(
        REMOTE_SSH_HOST="example.com",
        REMOTE_SSH_USER="deploy",
        REMOTE_PROJECT_ROOT="/srv/linuxclaw",
        REMOTE_ALLOWED_TEST_PREFIXES=["pytest", "python manage.py test"],
    )
    def test_run_test_command_rejects_disallowed_prefix(self):
        executor = RemoteExecutor()

        with self.assertRaises(RemoteExecutorError):
            executor.run_test_command("rm -rf /tmp/demo")


class RemotePlanSafetyTests(SimpleTestCase):
    def test_apply_change_plan_rejects_write_file_for_existing_file(self):
        class FakeExecutor:
            def assert_clean_worktree(self):
                return None

            def path_exists(self, path):
                return True

        with self.assertRaises(RemotePlanningError):
            apply_change_plan(
                FakeExecutor(),
                {
                    "summary": "test",
                    "edits": [
                        {
                            "type": "write_file",
                            "path": "blog/views.py",
                            "content": "print('bad')",
                        }
                    ],
                    "tests": [],
                },
            )


class TerminalWebViewTests(TestCase):
    def test_terminal_token_is_public_url_safe(self):
        token = create_terminal_access_token("oc_terminal_safe", profile="shell")

        self.assertNotIn(":", token)
        self.assertNotIn("/", token)

    def test_terminal_access_code_is_short_and_hex(self):
        code = create_terminal_access_code("oc_terminal_code", profile="shell")

        self.assertEqual(len(code), 12)
        self.assertRegex(code, r"^[0-9a-f]+$")

    def test_terminal_page_renders_with_valid_token(self):
        session = FeishuChatSession.objects.create(
            chat_id="oc_terminal_1",
            user_open_id="ou_x",
            memory={"terminal": {"active": True, "profile": "shell"}},
        )
        token = create_terminal_access_token(session.chat_id, profile="shell")

        response = self.client.get(reverse("terminal-page", kwargs={"token": token}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Linux Terminal")
        self.assertContains(response, "/blog/ws/terminal/")

    def test_terminal_short_page_renders_with_valid_code(self):
        session = FeishuChatSession.objects.create(
            chat_id="oc_terminal_short",
            user_open_id="ou_x",
            memory={"terminal": {"active": True, "profile": "shell"}},
        )
        code = create_terminal_access_code(session.chat_id, profile="shell")

        response = self.client.get(reverse("terminal-short-page", kwargs={"code": code}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Linux Terminal")
        self.assertTrue(TerminalAccessLink.objects.filter(code=code, chat_id=session.chat_id).exists())

    def test_terminal_short_page_accepts_unique_code_prefix(self):
        session = FeishuChatSession.objects.create(
            chat_id="oc_terminal_prefix",
            user_open_id="ou_x",
            memory={"terminal": {"active": True, "profile": "shell"}},
        )
        code = create_terminal_access_code(session.chat_id, profile="shell")

        response = self.client.get(reverse("terminal-short-page", kwargs={"code": code[:7]}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Linux Terminal")

    @patch("blog.views.RemoteTerminalManager")
    def test_terminal_api_returns_snapshot(self, terminal_manager_cls):
        session = FeishuChatSession.objects.create(
            chat_id="oc_terminal_2",
            user_open_id="ou_x",
            memory={"terminal": {"active": True, "profile": "codex"}},
        )
        terminal_manager = terminal_manager_cls.return_value
        terminal_manager.status.return_value = {
            "exists": True,
            "cwd": "/opt/linuxclaw",
            "program": "node",
            "output": "codex output",
        }
        token = create_terminal_access_token(session.chat_id, profile="codex")

        response = self.client.get(reverse("terminal-api", kwargs={"token": token}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["active"])
        self.assertEqual(payload["profile"], "codex")
        self.assertEqual(payload["cwd"], "/opt/linuxclaw")
        self.assertEqual(payload["program"], "node")


class TerminalWebsocketTests(TransactionTestCase):
    @override_settings(TERMINAL_WEBSOCKET_POLL_SECONDS=60)
    @patch("blog.consumers._terminal_status_snapshot")
    @patch("blog.consumers._terminal_send_snapshot")
    def test_terminal_socket_streams_snapshot_and_command_result(
        self,
        send_snapshot_mock,
        status_snapshot_mock,
    ):
        session = FeishuChatSession.objects.create(
            chat_id="oc_terminal_ws_1",
            user_open_id="ou_x",
            memory={"terminal": {"active": True, "profile": "shell"}},
        )
        token = create_terminal_access_token(session.chat_id, profile="shell")

        status_snapshot_mock.return_value = {
            "exists": True,
            "cwd": "/opt/linuxclaw",
            "program": "bash",
            "output": "ready",
        }
        send_snapshot_mock.return_value = {
            "exists": True,
            "cwd": "/opt/linuxclaw",
            "program": "bash",
            "output": "ready\npwd\n/opt/linuxclaw",
        }

        async def scenario():
            communicator = WebsocketCommunicator(
                application,
                f"/blog/ws/terminal/{token}/",
                headers=[
                    (b"host", b"testserver"),
                    (b"origin", b"http://testserver"),
                ],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            first_payload = await communicator.receive_json_from()
            self.assertTrue(first_payload["ok"])
            self.assertTrue(first_payload["active"])
            self.assertTrue(first_payload["replace"])
            self.assertEqual(first_payload["profile"], "shell")
            self.assertEqual(first_payload["cwd"], "/opt/linuxclaw")

            await communicator.send_json_to({"action": "send", "text": "pwd"})
            second_payload = await communicator.receive_json_from()
            self.assertTrue(second_payload["ok"])
            self.assertFalse(second_payload["replace"])
            self.assertIn("/opt/linuxclaw", second_payload["output"])

            await communicator.disconnect()

        async_to_sync(scenario)()


class TerminalOriginValidatorTests(SimpleTestCase):
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_terminal_origin_validator_allows_missing_origin(self):
        async def app(scope, receive, send):
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close"})

        communicator = WebsocketCommunicator(
            terminal_origin_validator(app),
            "/blog/ws/terminal/test-token/",
            headers=[(b"host", b"testserver")],
        )

        connected, _ = async_to_sync(communicator.connect)()
        self.assertTrue(connected)
        async_to_sync(communicator.disconnect)()

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_terminal_origin_validator_allows_null_origin(self):
        async def app(scope, receive, send):
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close"})

        communicator = WebsocketCommunicator(
            terminal_origin_validator(app),
            "/blog/ws/terminal/test-token/",
            headers=[(b"host", b"testserver"), (b"origin", b"null")],
        )

        connected, _ = async_to_sync(communicator.connect)()
        self.assertTrue(connected)
        async_to_sync(communicator.disconnect)()

    @override_settings(
        ALLOWED_HOSTS=["testserver"],
        TERMINAL_WEBSOCKET_ALLOWED_ORIGINS=["https://.feishu.cn"],
    )
    def test_terminal_origin_validator_allows_configured_feishu_origin(self):
        async def app(scope, receive, send):
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close"})

        communicator = WebsocketCommunicator(
            terminal_origin_validator(app),
            "/blog/ws/terminal/test-token/",
            headers=[(b"host", b"testserver"), (b"origin", b"https://applink.feishu.cn")],
        )

        connected, _ = async_to_sync(communicator.connect)()
        self.assertTrue(connected)
        async_to_sync(communicator.disconnect)()

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_terminal_origin_validator_rejects_untrusted_origin(self):
        async def app(scope, receive, send):
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close"})

        communicator = WebsocketCommunicator(
            terminal_origin_validator(app),
            "/blog/ws/terminal/test-token/",
            headers=[(b"host", b"testserver"), (b"origin", b"https://evil.example.com")],
        )

        connected, _ = async_to_sync(communicator.connect)()
        self.assertFalse(connected)
