import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Post
from .openai_client import OpenAIRequestError


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
        self.assertContains(response, "Noah Brooks")

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

    @patch("blog.views.create_chat_response")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_chat_page_sets_csrf_cookie_and_post_succeeds(self, create_chat_response_mock):
        create_chat_response_mock.return_value = {
            "response_id": "resp_123",
            "model": "gpt-5.4",
            "text": "This is the assistant reply.",
        }

        client = Client(enforce_csrf_checks=True)
        page_response = client.get(reverse("chat-page"))

        self.assertEqual(page_response.status_code, 200)
        self.assertIn("csrftoken", page_response.cookies)

        csrf_token = page_response.cookies["csrftoken"].value
        response = client.post(
            reverse("chat-api"),
            data=json.dumps({"message": "hello from csrf", "verbosity": "medium"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "This is the assistant reply.")

    @override_settings(OPENAI_API_KEY="")
    def test_chat_api_requires_api_key(self):
        response = self.client.post(
            reverse("chat-api"),
            data=json.dumps({"message": "hello"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"],
            "AI chat is unavailable because the API key is not configured.",
        )

    def test_chat_api_rejects_invalid_reasoning_effort(self):
        response = self.client.post(
            reverse("chat-api"),
            data=json.dumps({"message": "hello", "reasoning_effort": "ultra"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid reasoning effort value.")

    @patch("blog.views.create_chat_response")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_chat_api_returns_assistant_reply(self, create_chat_response_mock):
        create_chat_response_mock.return_value = {
            "response_id": "resp_123",
            "model": "gpt-5.4",
            "text": "This is the assistant reply.",
        }

        response = self.client.post(
            reverse("chat-api"),
            data=json.dumps(
                {
                    "message": "hello",
                    "reasoning_effort": "high",
                    "verbosity": "medium",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "This is the assistant reply.")
        create_chat_response_mock.assert_called_once_with(
            "hello",
            reasoning_effort="high",
            verbosity="medium",
        )

    @patch("blog.views.create_chat_response")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_chat_api_returns_gateway_error_for_timeout(self, create_chat_response_mock):
        create_chat_response_mock.side_effect = OpenAIRequestError(
            "Request to API timed out after 60 seconds."
        )

        response = self.client.post(
            reverse("chat-api"),
            data=json.dumps({"message": "hello"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["error"],
            "The AI response timed out. Please try again.",
        )

    @patch("blog.views.create_chat_response")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_chat_api_returns_service_unavailable_for_upstream_failure(self, create_chat_response_mock):
        create_chat_response_mock.side_effect = OpenAIRequestError(
            "OpenAI API error 502: bad gateway"
        )

        response = self.client.post(
            reverse("chat-api"),
            data=json.dumps({"message": "hello"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["error"],
            "The AI service is temporarily unavailable. Please try again later.",
        )
