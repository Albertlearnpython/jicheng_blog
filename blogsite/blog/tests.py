import json
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .codex_client import CodexExecutionError, CodexSSHClient, CodexTurnResult
from .feishu_views import _extract_text_message, _resolve_codex_execution_policy, process_feishu_event
from .models import FeishuChatSession
from .translation_client import maybe_translate_user_message


class ServiceViewTests(TestCase):
    def test_health_endpoint_returns_ok(self):
        response = self.client.get(reverse("service-health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "service": "linuxclaw-codex-bot",
            },
        )


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

    @patch("blog.feishu_views.start_event_processing")
    @override_settings(FEISHU_VERIFICATION_TOKEN="verify-123")
    def test_legacy_blog_prefixed_event_path_starts_background_processing(
        self,
        start_event_processing_mock,
    ):
        payload = {
            "schema": "2.0",
            "header": {
                "event_id": "evt_legacy_1",
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
                    "message_id": "om_legacy_1",
                    "chat_id": "oc_legacy_1",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": "hello"}, ensure_ascii=False),
                    "mentions": [],
                },
            },
        }

        response = self.client.post(
            "/blog/api/feishu/events/",
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
                "content": json.dumps({"text": "@linuxclaw 帮我看看这个问题"}, ensure_ascii=False),
                "mentions": [{"name": "linuxclaw"}],
            }
        }

        self.assertEqual(_extract_text_message(event), "帮我看看这个问题")

    def test_maybe_translate_user_message_converts_chinese_to_pinyin_prompt(self):
        adapted = maybe_translate_user_message("你好，介绍一下你自己。")

        self.assertIn("Mandarin Chinese", adapted)
        self.assertIn("Pinyin message:", adapted)
        self.assertIn("ni hao", adapted)

    @override_settings(
        CODEX_BIN="codex",
        CODEX_PROFILE="",
        CODEX_MODEL="gpt-5.4",
        CODEX_REASONING_EFFORT="xhigh",
        CODEX_SANDBOX="danger-full-access",
        CODEX_WORKDIR="/root",
        CODEX_DISABLE_RESPONSE_STORAGE=True,
    )
    def test_codex_resume_command_omits_unsupported_flags(self):
        command = CodexSSHClient()._build_command(thread_id="thread_123")

        self.assertIn("exec resume", command)
        self.assertNotIn("--sandbox", command)
        self.assertNotIn("--cd", command)
        self.assertIn("thread_123", command)

    @override_settings(
        CODEX_SANDBOX="danger-full-access",
        CODEX_WORKDIR="/root",
        CODEX_RESTRICTED_SANDBOX="read-only",
        CODEX_RESTRICTED_WORKDIR="/opt/linuxclaw-release",
        CODEX_PRIVILEGED_CHAT_IDS=["oc_privileged"],
        CODEX_PRIVILEGED_OPEN_IDS=["ou_privileged"],
    )
    def test_resolve_codex_execution_policy_returns_privileged_for_allowed_private_chat(self):
        policy = _resolve_codex_execution_policy("oc_privileged", "ou_other", "p2p")

        self.assertEqual(policy["label"], "privileged")
        self.assertEqual(policy["sandbox"], "danger-full-access")
        self.assertEqual(policy["workdir"], "/root")

    @override_settings(
        CODEX_SANDBOX="danger-full-access",
        CODEX_WORKDIR="/root",
        CODEX_RESTRICTED_SANDBOX="read-only",
        CODEX_RESTRICTED_WORKDIR="/opt/linuxclaw-release",
        CODEX_PRIVILEGED_CHAT_IDS=["oc_privileged"],
        CODEX_PRIVILEGED_OPEN_IDS=["ou_privileged"],
    )
    def test_resolve_codex_execution_policy_keeps_group_chat_restricted(self):
        policy = _resolve_codex_execution_policy("oc_group", "ou_privileged", "group")

        self.assertEqual(policy["label"], "restricted")
        self.assertEqual(policy["sandbox"], "read-only")
        self.assertEqual(policy["workdir"], "/opt/linuxclaw-release")


class FeishuRoutingTests(TestCase):
    def _payload(self, text, *, mentions=None, chat_type="p2p", chat_id="oc_1", open_id="ou_x"):
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
                    "sender_id": {"open_id": open_id},
                },
                "message": {
                    "message_id": "om_1",
                    "chat_id": chat_id,
                    "chat_type": chat_type,
                    "message_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                    "mentions": mentions or [],
                },
            },
        }

    @patch("blog.feishu_views._send_chat_reply")
    @patch("blog.feishu_views.maybe_translate_user_message")
    @patch("blog.feishu_views.CodexSSHClient.run_turn")
    @override_settings(
        CODEX_SANDBOX="danger-full-access",
        CODEX_WORKDIR="/root",
        CODEX_RESTRICTED_SANDBOX="read-only",
        CODEX_RESTRICTED_WORKDIR="/opt/linuxclaw-release",
        CODEX_PRIVILEGED_CHAT_IDS=["oc_privileged"],
        CODEX_PRIVILEGED_OPEN_IDS=[],
    )
    def test_privileged_private_chat_uses_danger_full_access(
        self,
        run_turn_mock,
        translate_mock,
        send_chat_reply_mock,
    ):
        translate_mock.return_value = "hello"
        run_turn_mock.return_value = CodexTurnResult(
            thread_id="thread_privileged",
            reply_text="第一条回复",
        )

        process_feishu_event(self._payload("你好", chat_id="oc_privileged"))

        run_turn_mock.assert_called_once_with(
            "hello",
            thread_id="",
            sandbox="danger-full-access",
            workdir="/root",
        )
        send_chat_reply_mock.assert_called_once_with("oc_privileged", "om_1", "第一条回复")

    @patch("blog.feishu_views._send_chat_reply")
    @patch("blog.feishu_views.maybe_translate_user_message")
    @patch("blog.feishu_views.CodexSSHClient.run_turn")
    @override_settings(
        CODEX_SANDBOX="danger-full-access",
        CODEX_WORKDIR="/root",
        CODEX_RESTRICTED_SANDBOX="read-only",
        CODEX_RESTRICTED_WORKDIR="/opt/linuxclaw-release",
        CODEX_PRIVILEGED_CHAT_IDS=["oc_privileged"],
        CODEX_PRIVILEGED_OPEN_IDS=[],
    )
    def test_other_private_chats_stay_read_only(
        self,
        run_turn_mock,
        translate_mock,
        send_chat_reply_mock,
    ):
        translate_mock.return_value = "hello"
        run_turn_mock.return_value = CodexTurnResult(
            thread_id="thread_restricted",
            reply_text="第一条回复",
        )

        process_feishu_event(self._payload("你好", chat_id="oc_other"))

        run_turn_mock.assert_called_once_with(
            "hello",
            thread_id="",
            sandbox="read-only",
            workdir="/opt/linuxclaw-release",
        )
        send_chat_reply_mock.assert_called_once_with("oc_other", "om_1", "第一条回复")

    @patch("blog.feishu_views._send_chat_reply")
    @patch("blog.feishu_views.maybe_translate_user_message")
    @patch("blog.feishu_views.CodexSSHClient.run_turn")
    def test_message_starts_new_codex_thread_and_persists_session(
        self,
        run_turn_mock,
        translate_mock,
        send_chat_reply_mock,
    ):
        translate_mock.return_value = "hello"
        run_turn_mock.return_value = CodexTurnResult(
            thread_id="thread_1",
            reply_text="第一条回复",
        )

        process_feishu_event(self._payload("你好"))

        translate_mock.assert_called_once_with("你好")
        session = FeishuChatSession.objects.get(chat_id="oc_1")
        self.assertEqual(session.codex_thread_id, "thread_1")
        self.assertEqual(session.last_user_message, "你好")
        self.assertEqual(session.last_assistant_message, "第一条回复")
        send_chat_reply_mock.assert_called_once_with("oc_1", "om_1", "第一条回复")

    @patch("blog.feishu_views._send_chat_reply")
    @patch("blog.feishu_views.maybe_translate_user_message")
    @patch("blog.feishu_views.CodexSSHClient.run_turn")
    def test_message_resumes_existing_codex_thread(
        self,
        run_turn_mock,
        translate_mock,
        send_chat_reply_mock,
    ):
        FeishuChatSession.objects.create(
            chat_id="oc_1",
            user_open_id="ou_x",
            codex_thread_id="thread_existing",
        )
        translate_mock.return_value = "continue the last topic"
        run_turn_mock.return_value = CodexTurnResult(
            thread_id="thread_existing",
            reply_text="续接后的回复",
        )

        process_feishu_event(self._payload("继续上一轮问题"))

        translate_mock.assert_called_once_with("继续上一轮问题")
        run_turn_mock.assert_called_once_with(
            "continue the last topic",
            thread_id="thread_existing",
            sandbox="read-only",
            workdir="/root",
        )
        send_chat_reply_mock.assert_called_once_with("oc_1", "om_1", "续接后的回复")

    @override_settings(FEISHU_REQUIRE_GROUP_MENTION=True)
    @patch("blog.feishu_views.CodexSSHClient.run_turn")
    def test_group_message_without_mention_is_ignored(self, run_turn_mock):
        process_feishu_event(self._payload("群消息", chat_type="group"))

        run_turn_mock.assert_not_called()
        self.assertFalse(FeishuChatSession.objects.exists())

    @patch("blog.feishu_views._send_chat_reply")
    @patch("blog.feishu_views.maybe_translate_user_message")
    @patch("blog.feishu_views.CodexSSHClient.run_turn")
    def test_codex_error_is_returned_to_user(
        self,
        run_turn_mock,
        translate_mock,
        send_chat_reply_mock,
    ):
        translate_mock.return_value = "hello"
        run_turn_mock.side_effect = CodexExecutionError("resume failed")

        process_feishu_event(self._payload("你好"))

        send_chat_reply_mock.assert_called_once()
        self.assertIn("Codex 调用失败", send_chat_reply_mock.call_args.args[2])
