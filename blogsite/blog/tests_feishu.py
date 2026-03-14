import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .feishu_views import _extract_text_message, process_feishu_event
from .remote_agent import (
    INTENT_CHAT,
    INTENT_REPO,
    RemotePlanningError,
    apply_change_plan,
    classify_user_request,
    format_plan_for_user,
)
from .remote_executor import RemoteExecutor, RemoteExecutorError


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


class FeishuRoutingTests(TestCase):
    def _payload(self, text):
        return {
            "schema": "2.0",
            "header": {
                "event_id": f"evt_{text}",
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
        answer_general_question_mock.assert_called_once_with("你是谁")
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
