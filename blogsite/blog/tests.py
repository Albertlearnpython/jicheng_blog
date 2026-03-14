import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Post


class BlogViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user(username="tester", password="secret123")
        cls.post = Post.objects.create(
            title="Django article",
            content="# Heading\n\nThis is a detailed article body for the blog.",
            author=cls.author,
        )

    def test_landing_page_links_to_blog(self):
        response = self.client.get(reverse("site-home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("blog-home"))
        self.assertContains(response, "Hello, I&#x27;m Albert.")

    def test_home_page_shows_post_and_detail_link(self):
        response = self.client.get(reverse("blog-home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.post.title)
        self.assertContains(response, reverse("post-detail", args=[self.post.pk]))

    def test_post_detail_page_shows_full_content(self):
        response = self.client.get(reverse("post-detail", args=[self.post.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.post.title)
        self.assertContains(response, "This is a detailed article body for the blog.")

    def test_missing_post_detail_returns_404(self):
        response = self.client.get(reverse("post-detail", args=[9999]))

        self.assertEqual(response.status_code, 404)

    def test_chat_page_loads(self):
        response = self.client.get(reverse("chat-page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI 实验室")

    @override_settings(OPENAI_API_KEY="")
    def test_chat_api_requires_api_key(self):
        response = self.client.post(
            reverse("chat-api"),
            data=json.dumps({"message": "你好"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "OPENAI_API_KEY is not configured.")

    def test_chat_api_rejects_invalid_reasoning_effort(self):
        response = self.client.post(
            reverse("chat-api"),
            data=json.dumps({"message": "你好", "reasoning_effort": "ultra"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "推理强度不合法。")

    @patch("blog.views.create_chat_response")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_chat_api_returns_assistant_reply(self, create_chat_response_mock):
        create_chat_response_mock.return_value = {
            "response_id": "resp_123",
            "model": "gpt-5.4",
            "text": "这是接口返回的内容。",
        }

        response = self.client.post(
            reverse("chat-api"),
            data=json.dumps(
                {
                    "message": "你好",
                    "reasoning_effort": "high",
                    "verbosity": "medium",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "这是接口返回的内容。")
        create_chat_response_mock.assert_called_once_with(
            "你好",
            reasoning_effort="high",
            verbosity="medium",
        )

    @patch("blog.views.create_chat_response")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_chat_api_returns_gateway_error_for_timeout(self, create_chat_response_mock):
        from .openai_client import OpenAIRequestError

        create_chat_response_mock.side_effect = OpenAIRequestError(
            "Request to API timed out after 60 seconds."
        )

        response = self.client.post(
            reverse("chat-api"),
            data=json.dumps({"message": "你好"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["error"],
            "Request to API timed out after 60 seconds.",
        )
