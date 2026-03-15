import json
import logging
import math
import re
from html import escape

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.http import require_http_methods

from .models import Post
from .openai_client import OpenAIConfigError, OpenAIRequestError, create_chat_response
from .remote_executor import RemoteExecutorError
from .remote_terminal import RemoteTerminalError, RemoteTerminalManager
from .terminal_state import (
    TerminalSessionError,
    clear_terminal_state,
    get_terminal_state,
    resolve_terminal_session,
    terminal_snapshot_payload,
    update_terminal_state,
)
from .terminal_web import (
    build_terminal_ws_path,
    create_terminal_access_token,
    resolve_terminal_access_code,
)

logger = logging.getLogger(__name__)

VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
VALID_VERBOSITY = {"low", "medium", "high"}
WORD_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

PROFILE = {
    "name": "孙伯符",
    "handle": "Noah Brooks",
    "role": "Python / AI / 阅读",
    "location": "深圳",
    "organization": "华南农业大学 · 本科",
    "summary": "一个喜欢读书的人。",
    "hero_title": "孙伯符 / Noah Brooks",
    "hero_subtitle": "在深圳记录 Python、AI、阅读与持续搭建，把零散的学习过程整理成长期作品。",
    "motto": "静水流深，金石为开；守拙见慧，癸水逢源。",
    "avatar_asset": "blog/avatar-wechat.jpg",
    "avatar_label": "孙",
    "contact_label": "微信",
    "contact_value": "djm13126042156",
    "tags": [
        "Python",
        "AI",
        "阅读",
        "深圳",
        "华南农业大学",
    ],
}

TIMELINE = [
    {"date": "2026-03-14", "title": "集成博客网站创建"},
    {"date": "进行中", "title": "持续记录 Python、AI 与阅读笔记"},
    {"date": "下一步", "title": "补充更多个人文章、项目与长期思考"},
]

INTRO_COLUMNS = [
    {
        "title": "关于我",
        "items": [
            "中文名孙伯符，英文名 Noah Brooks。",
            "现在在深圳，学校是华南农业大学，本科阶段。",
            "平时喜欢读书，也喜欢把思考慢慢写下来。",
        ],
    },
    {
        "title": "关注主题",
        "items": [
            "Python 学习与项目实践。",
            "AI 工具、模型体验和应用记录。",
            "阅读、笔记整理与长期积累。",
        ],
    },
    {
        "title": "站点方向",
        "items": [
            "把博客做成个人主页、文章列表和实验空间的统一入口。",
            "保留内容优先的阅读体验，也保留好看的前端细节。",
            "继续补充真实项目、个人介绍和更多文章归档。",
        ],
    },
]

FAQS = [
    {
        "question": "这个博客会主要记录什么内容？",
        "answer": "会围绕 Python、AI、阅读和个人成长持续更新，既写技术，也写过程中的思考和沉淀。",
    },
    {
        "question": "为什么首页和博客页风格做得比较强？",
        "answer": "因为这个站点既是博客，也是个人展示页。保留主题切换、音效和动态文字，是为了让它更像一个有个人气质的长期作品。",
    },
    {
        "question": "怎么联系你？",
        "answer": "目前可以通过微信联系我，微信号是 djm13126042156。后面也会继续补充更多社交链接和项目入口。",
    },
]


def _launch_date(posts):
    if posts:
        oldest = min(posts, key=lambda item: item.date_posted)
        return oldest.date_posted
    return timezone.now()


def _plain_text(content):
    text = strip_tags(content or "")
    text = text.replace("\r\n", "\n")
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*-\s*\[[ xX]\]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"[`*_~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _word_count(text):
    return len(WORD_TOKEN_PATTERN.findall(text))


def _reading_minutes(word_count):
    return max(1, math.ceil(max(word_count, 1) / 220))


def _post_kind(title, plain_text):
    haystack = f"{title} {plain_text}".lower()
    if "ai" in haystack or "gpt" in haystack:
        return "AI 实验"
    if "git" in haystack or "github" in haystack or "django" in haystack:
        return "开发日志"
    if "总结" in haystack or "年度" in haystack:
        return "年度回顾"
    if "软件" in haystack or "下载" in haystack or "工具" in haystack:
        return "工具分享"
    return "技术笔记"


def _cover_label(title):
    cleaned = re.sub(r"\s+", "", title or "")
    return cleaned[:2] or "文"


def _inline_format(text):
    formatted = escape(text)
    formatted = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        lambda match: (
            f'<a href="{escape(match.group(2), quote=True)}" '
            f'target="_blank" rel="noreferrer">{match.group(1)}</a>'
        ),
        formatted,
    )
    formatted = re.sub(r"`([^`]+)`", r"<code>\1</code>", formatted)
    formatted = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", formatted)
    formatted = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", formatted)
    return formatted


def _render_markdownish(content):
    lines = (content or "").replace("\r\n", "\n").split("\n")
    html_parts = []
    paragraph_buffer = []
    quote_buffer = []
    list_buffer = []
    code_buffer = []
    in_code = False

    def flush_paragraph():
        if not paragraph_buffer:
            return
        body = "<br>".join(_inline_format(line) for line in paragraph_buffer)
        html_parts.append(f"<p>{body}</p>")
        paragraph_buffer.clear()

    def flush_quote():
        if not quote_buffer:
            return
        body = "".join(f"<p>{_inline_format(line)}</p>" for line in quote_buffer)
        html_parts.append(f"<blockquote>{body}</blockquote>")
        quote_buffer.clear()

    def flush_list():
        if not list_buffer:
            return
        html_parts.append(f'<ul class="article-list">{"".join(list_buffer)}</ul>')
        list_buffer.clear()

    def flush_code():
        nonlocal in_code
        if not code_buffer:
            in_code = False
            return
        block = escape("\n".join(code_buffer))
        html_parts.append(f"<pre><code>{block}</code></pre>")
        code_buffer.clear()
        in_code = False

    for raw_line in lines:
        stripped = raw_line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            flush_quote()
            flush_list()
            if in_code:
                flush_code()
            else:
                in_code = True
            continue

        if in_code:
            code_buffer.append(raw_line)
            continue

        if not stripped:
            flush_paragraph()
            flush_quote()
            flush_list()
            continue

        heading_match = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        checkbox_match = re.match(r"^- \[([ xX])\]\s+(.*)$", stripped)
        bullet_match = re.match(r"^[-*]\s+(.*)$", stripped)

        if heading_match:
            flush_paragraph()
            flush_quote()
            flush_list()
            level = min(len(heading_match.group(1)) + 1, 5)
            title = _inline_format(heading_match.group(2).strip())
            html_parts.append(f"<h{level}>{title}</h{level}>")
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            quote_buffer.append(stripped.lstrip(">").strip())
            continue

        if checkbox_match:
            flush_paragraph()
            flush_quote()
            checked = checkbox_match.group(1).lower() == "x"
            label = _inline_format(checkbox_match.group(2).strip())
            css_class = " is-done" if checked else ""
            mark = "已完成" if checked else "待完成"
            list_buffer.append(
                f'<li class="check-item{css_class}"><span>{label}</span><em>{mark}</em></li>'
            )
            continue

        if bullet_match:
            flush_paragraph()
            flush_quote()
            list_buffer.append(f"<li>{_inline_format(bullet_match.group(1).strip())}</li>")
            continue

        flush_quote()
        flush_list()
        paragraph_buffer.append(stripped)

    flush_paragraph()
    flush_quote()
    flush_list()
    flush_code()

    if not html_parts:
        html_parts.append("<p>这篇文章暂时还没有正文。</p>")
    return mark_safe("".join(html_parts))


def _build_post_card(post):
    plain_text = _plain_text(post.content)
    word_count = _word_count(plain_text)
    excerpt = plain_text[:180]
    if len(plain_text) > 180:
        excerpt += "..."

    return {
        "pk": post.pk,
        "title": post.title,
        "author": post.author.username,
        "date_posted": post.date_posted,
        "plain_text": plain_text,
        "excerpt": excerpt,
        "word_count": word_count,
        "reading_minutes": _reading_minutes(word_count),
        "kind": _post_kind(post.title, plain_text),
        "cover_label": _cover_label(post.title),
        "rendered_content": _render_markdownish(post.content),
    }


def _site_context(posts):
    cards = [_build_post_card(post) for post in posts]
    latest_post = cards[0] if cards else None
    launch_date = _launch_date(posts)
    return {
        "name": "孙伯符 / Noah Brooks",
        "blog_name": "孙伯符的博客",
        "description": "一个喜欢读书的人，在深圳记录 Python、AI 与长期思考的个人博客。",
        "owner": PROFILE["name"],
        "owner_en": PROFILE["handle"],
        "role": PROFILE["role"],
        "location": PROFILE["location"],
        "organization": PROFILE["organization"],
        "contact_label": PROFILE["contact_label"],
        "contact_value": PROFILE["contact_value"],
        "brand_mark": PROFILE["avatar_label"],
        "launch_date": launch_date,
        "launch_iso": launch_date.isoformat(),
        "post_count": len(cards),
        "author_count": len({post.author_id for post in posts}),
        "total_words": sum(card["word_count"] for card in cards),
        "latest_post": latest_post,
    }


def _landing_projects():
    return [
        {
            "title": "博客",
            "description": "浏览文章列表、详情页与搜索结果，是站点的核心内容区。",
            "href": reverse("blog-home"),
            "eyebrow": "Blog",
        },
        {
            "title": "AI Chat",
            "description": "接入 OpenAI Responses API 的实验性问答页，也是后续 AI 功能扩展入口。",
            "href": reverse("chat-page"),
            "eyebrow": "Lab",
        },
        {
            "title": "GitHub Repo",
            "description": "查看源码、提交记录和这个博客的持续迭代进展。",
            "href": "https://github.com/Albertlearnpython/jicheng_blog",
            "eyebrow": "Source",
        },
        {
            "title": "Admin",
            "description": "通过 Django 后台管理文章、内容和后续扩展的数据结构。",
            "href": reverse("admin:index"),
            "eyebrow": "Manage",
        },
    ]


def landing(request):
    posts = list(Post.objects.select_related("author").order_by("-date_posted"))
    recent_posts = [_build_post_card(post) for post in posts[:3]]
    site = _site_context(posts)

    context = {
        "page_key": "landing",
        "profile": PROFILE,
        "timeline": TIMELINE,
        "intro_columns": INTRO_COLUMNS,
        "faqs": FAQS,
        "projects": _landing_projects(),
        "recent_posts": recent_posts,
        "site": site,
        "landing_stats": [
            {"label": "文章数", "value": site["post_count"]},
            {"label": "作者数", "value": site["author_count"]},
            {"label": "累计字数", "value": site["total_words"]},
        ],
    }
    return render(request, "blog/landing.html", context)


def home(request):
    all_posts_qs = Post.objects.select_related("author").order_by("-date_posted")
    all_posts = list(all_posts_qs)
    query = (request.GET.get("q") or "").strip()

    filtered_qs = all_posts_qs
    if query:
        filtered_qs = filtered_qs.filter(
            Q(title__icontains=query)
            | Q(content__icontains=query)
            | Q(author__username__icontains=query)
        )

    paginator = Paginator(filtered_qs, 6)
    page_obj = paginator.get_page(request.GET.get("page"))
    post_cards = [_build_post_card(post) for post in page_obj.object_list]
    site = _site_context(all_posts)

    context = {
        "page_key": "blog",
        "site": site,
        "posts": post_cards,
        "page_obj": page_obj,
        "query": query,
        "filtered_count": filtered_qs.count(),
        "feature_blocks": [
            {"label": "文章列表", "value": site["post_count"], "hint": "持续更新中"},
            {"label": "关注方向", "value": "Python / AI", "hint": "也会写阅读与思考"},
            {"label": "主题样式", "value": "可切换", "hint": "明暗 / 字体 / 密度 / 音效"},
        ],
    }
    return render(request, "blog/home.html", context)


def post_detail(request, pk):
    post = get_object_or_404(Post.objects.select_related("author"), pk=pk)
    all_posts = list(Post.objects.select_related("author").order_by("-date_posted"))
    site = _site_context(all_posts)
    post_card = _build_post_card(post)
    related_posts = [
        _build_post_card(item) for item in all_posts if item.pk != post.pk
    ][:3]

    context = {
        "page_key": "detail",
        "site": site,
        "post": post,
        "post_card": post_card,
        "related_posts": related_posts,
    }
    return render(request, "blog/post_detail.html", context)

def _chat_error_payload(exc):
    detail = str(exc).strip()
    lowered = detail.lower()

    if isinstance(exc, OpenAIConfigError):
        return (
            {"error": "AI chat is unavailable because the API key is not configured."},
            503,
        )

    if "timed out" in lowered:
        return (
            {"error": "The AI response timed out. Please try again."},
            502,
        )

    if "network error" in lowered:
        return (
            {"error": "Network error while contacting the AI service. Please try again."},
            502,
        )

    return (
        {"error": "The AI service is temporarily unavailable. Please try again later."},
        502,
    )


@ensure_csrf_cookie
@require_GET
def chat_page(request):
    posts = list(Post.objects.select_related("author").order_by("-date_posted"))
    site = _site_context(posts)
    return render(
        request,
        "blog/chat.html",
        {
            "page_key": "chat",
            "site": site,
            "chat_model": settings.OPENAI_MODEL,
            "default_effort": settings.OPENAI_REASONING_EFFORT,
            "default_verbosity": settings.OPENAI_TEXT_VERBOSITY,
            "api_ready": bool(settings.OPENAI_API_KEY),
            "chat_api_url": reverse("chat-api"),
            "reasoning_options": ["none", "minimal", "low", "medium", "high", "xhigh"],
            "verbosity_options": ["low", "medium", "high"],
        },
    )


@require_POST
def chat_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON。"}, status=400)

    message = (payload.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "请输入问题后再发送。"}, status=400)

    if len(message) > 4000:
        return JsonResponse({"error": "问题过长，请控制在 4000 字符以内。"}, status=400)

    reasoning_effort = (payload.get("reasoning_effort") or "").strip() or None
    verbosity = (payload.get("verbosity") or "").strip() or None

    if reasoning_effort and reasoning_effort not in VALID_REASONING_EFFORTS:
        return JsonResponse({"error": "推理强度不合法。"}, status=400)

    if verbosity and verbosity not in VALID_VERBOSITY:
        return JsonResponse({"error": "详细程度不合法。"}, status=400)

    try:
        response_data = create_chat_response(
            message,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
        )
    except OpenAIConfigError as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    except OpenAIRequestError as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    return JsonResponse(response_data)


@require_POST
def chat_api_v2(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)

    message = (payload.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "Enter a question before sending the request."}, status=400)

    if len(message) > 4000:
        return JsonResponse({"error": "Questions must be 4000 characters or fewer."}, status=400)

    reasoning_effort = (payload.get("reasoning_effort") or "").strip() or None
    verbosity = (payload.get("verbosity") or "").strip() or None

    if reasoning_effort and reasoning_effort not in VALID_REASONING_EFFORTS:
        return JsonResponse({"error": "Invalid reasoning effort value."}, status=400)

    if verbosity and verbosity not in VALID_VERBOSITY:
        return JsonResponse({"error": "Invalid verbosity value."}, status=400)

    try:
        response_data = create_chat_response(
            message,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
        )
    except (OpenAIConfigError, OpenAIRequestError) as exc:
        payload, status_code = _chat_error_payload(exc)
        return JsonResponse(payload, status=status_code)
    except Exception:
        logger.exception("Unexpected error while handling AI chat request")
        return JsonResponse(
            {"error": "An unexpected error occurred while processing the AI request."},
            status=500,
        )

    return JsonResponse(response_data)


def _terminal_page_site():
    posts = list(Post.objects.select_related("author").order_by("-date_posted"))
    return _site_context(posts)


@require_GET
def terminal_short_page(request, code):
    try:
        payload = resolve_terminal_access_code(code)
    except signing.BadSignature as exc:
        raise Http404("Invalid terminal shortcut.") from exc

    token = create_terminal_access_token(
        payload.get("chat_id", ""),
        profile=payload.get("profile", "shell"),
    )
    return terminal_page(request, token)


@require_GET
def terminal_page(request, token):
    try:
        session, payload = resolve_terminal_session(token)
    except TerminalSessionError as exc:
        raise Http404(str(exc)) from exc

    state = get_terminal_state(session)

    return render(
        request,
        "blog/terminal.html",
        {
            "page_key": "terminal",
            "site": _terminal_page_site(),
            "terminal_token": token,
            "terminal_api_url": reverse("terminal-api", kwargs={"token": token}),
            "terminal_ws_url": build_terminal_ws_path(token),
            "terminal_profile": payload.get("profile") or state.get("profile") or "shell",
            "terminal_active": bool(state.get("active")),
        },
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def terminal_api(request, token):
    try:
        session, payload = resolve_terminal_session(token)
    except TerminalSessionError as exc:
        raise Http404(str(exc)) from exc

    manager = RemoteTerminalManager()

    if request.method == "GET":
        try:
            snapshot = manager.status(session.chat_id)
        except (RemoteTerminalError, RemoteExecutorError) as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=502)

        if not snapshot.get("exists"):
            clear_terminal_state(session)
            return JsonResponse(
                {
                    "ok": True,
                    "active": False,
                    "profile": payload.get("profile") or "shell",
                    "cwd": "",
                    "program": "",
                    "output": "",
                    "passthrough": False,
                }
            )

        profile = get_terminal_state(session).get("profile") or payload.get("profile") or "shell"
        state = update_terminal_state(
            session,
            active=True,
            profile=profile,
            passthrough=True,
            cwd=snapshot.get("cwd", ""),
            program=snapshot.get("program", ""),
            output=snapshot.get("output", ""),
        )
        snapshot["profile"] = profile
        return JsonResponse(terminal_snapshot_payload(state, snapshot, fallback_profile=profile))

    try:
        request_payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Request body must be valid JSON."}, status=400)

    action = (request_payload.get("action") or "").strip().lower()
    try:
        if action == "send":
            snapshot = manager.send(session.chat_id, request_payload.get("text", ""), enter=True)
        elif action == "key":
            snapshot = manager.send_key(session.chat_id, request_payload.get("key", ""))
        elif action == "close":
            manager.close(session.chat_id)
            clear_terminal_state(session)
            return JsonResponse({"ok": True, "active": False, "closed": True})
        elif action == "open":
            profile = (request_payload.get("profile") or payload.get("profile") or "shell").strip().lower()
            snapshot = manager.open(session.chat_id, profile=profile, cwd=request_payload.get("cwd", ""))
            snapshot["profile"] = profile
        else:
            return JsonResponse({"ok": False, "error": "Unsupported terminal action."}, status=400)
    except (RemoteTerminalError, RemoteExecutorError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)

    profile = snapshot.get("profile") or payload.get("profile") or "shell"
    state = update_terminal_state(
        session,
        active=True,
        profile=profile,
        passthrough=True,
        cwd=snapshot.get("cwd", ""),
        program=snapshot.get("program", ""),
        output=snapshot.get("output", ""),
    )
    snapshot["profile"] = profile
    return JsonResponse(terminal_snapshot_payload(state, snapshot, fallback_profile=profile))
