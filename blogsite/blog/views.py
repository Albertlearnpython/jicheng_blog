import json
import math
import re
from html import escape

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_POST

from .models import Post
from .openai_client import OpenAIConfigError, OpenAIRequestError, create_chat_response

VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
VALID_VERBOSITY = {"low", "medium", "high"}
WORD_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

PROFILE = {
    "name": "Albert",
    "handle": "@Albertlearnpython",
    "role": "Python / AI / Web",
    "location": "Remote Builder",
    "organization": "Personal Lab",
    "summary": "把学习、搭建和写作整合成一个持续更新的个人数字花园。",
    "hero_title": "Hello, I'm Albert.",
    "hero_subtitle": "做博客，也做工具，把零散的学习过程整理成可回看的长期作品。",
    "motto": "把代码、思考和生活记录放在同一个可持续维护的网站里。",
    "tags": [
        "Django",
        "Python",
        "AI Workflow",
        "Frontend",
        "Writing",
        "Personal Site",
    ],
}

TIMELINE = [
    {"date": "Future", "title": "继续扩充博客与 AI 工具模块"},
    {"date": "2026-03-14", "title": "重做首页与博客视觉系统"},
    {"date": "2026-03-12", "title": "完成 Django 博客站点初版"},
    {"date": "Today", "title": "保持输出，持续迭代"},
]

INTRO_COLUMNS = [
    {
        "title": "Focus",
        "items": [
            "把博客首页和文章页做成统一的个人品牌站。",
            "记录 AI、编程、效率工具和独立折腾过程。",
            "让写作、展示和项目入口都落在同一个域名体系下。",
        ],
    },
    {
        "title": "Current Stack",
        "items": [
            "Django 5 + SQLite",
            "自定义 CSS 设计 token 与主题切换",
            "OpenAI Responses API 聊天实验页",
        ],
    },
    {
        "title": "Design Goals",
        "items": [
            "首页像个人名片与项目看板。",
            "博客页保留内容优先的阅读体验。",
            "移动端也能完整访问侧栏导航和主题设置。",
        ],
    },
]

FAQS = [
    {
        "question": "这个站点现在有哪些主要板块？",
        "answer": "目前包含个人首页、博客列表、文章详情页和 AI 聊天实验页。首页负责展示身份、项目入口与站点概览，博客页负责承载文章阅读。",
    },
    {
        "question": "博客页为什么保留了主题切换和布局控制？",
        "answer": "这样你后续不用改代码结构，只需要微调设计 token，就能快速切换明暗、字型和内容密度。",
    },
    {
        "question": "后面还适合扩展什么？",
        "answer": "可以继续加入分类、标签、归档、搜索增强、评论、图床和自动化部署等模块。",
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
        "name": "Albert 的博客站",
        "blog_name": "Albert Notes",
        "description": "一个受个人主页与聚合博客布局启发、用 Django 搭建的内容站点。",
        "owner": PROFILE["name"],
        "role": PROFILE["role"],
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
            "description": "浏览文章列表、详情页与搜索结果，是站点的主内容区。",
            "href": reverse("blog-home"),
            "eyebrow": "Blog",
        },
        {
            "title": "AI Chat",
            "description": "接入 OpenAI Responses API 的实验性问答页。",
            "href": reverse("chat-page"),
            "eyebrow": "Lab",
        },
        {
            "title": "GitHub Repo",
            "description": "查看源码、提交记录和后续迭代进展。",
            "href": "https://github.com/Albertlearnpython/jicheng_blog",
            "eyebrow": "Source",
        },
        {
            "title": "Admin",
            "description": "通过 Django 后台管理文章、用户和内容数据。",
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
            {"label": "搜索状态", "value": "已启用", "hint": "支持标题 / 正文"},
            {"label": "主题样式", "value": "可切换", "hint": "明暗 / 字体 / 密度"},
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
