import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from .models import Post
from .openai_client import OpenAIConfigError, OpenAIRequestError, create_chat_response

VALID_REASONING_EFFORTS = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh'}
VALID_VERBOSITY = {'low', 'medium', 'high'}


def home(request):
    posts = Post.objects.select_related('author').order_by('-date_posted')
    return render(request, 'blog/home.html', {'posts': posts})


def post_detail(request, pk):
    post = get_object_or_404(Post.objects.select_related('author'), pk=pk)
    return render(request, 'blog/post_detail.html', {'post': post})


@require_GET
def chat_page(request):
    return render(
        request,
        'blog/chat.html',
        {
            'chat_model': settings.OPENAI_MODEL,
            'default_effort': settings.OPENAI_REASONING_EFFORT,
            'default_verbosity': settings.OPENAI_TEXT_VERBOSITY,
            'api_ready': bool(settings.OPENAI_API_KEY),
        },
    )


@require_POST
def chat_api(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'error': '请求体不是合法 JSON。'}, status=400)

    message = (payload.get('message') or '').strip()
    if not message:
        return JsonResponse({'error': '请输入问题后再发送。'}, status=400)

    if len(message) > 4000:
        return JsonResponse({'error': '问题过长，请控制在 4000 个字符以内。'}, status=400)

    reasoning_effort = (payload.get('reasoning_effort') or '').strip() or None
    verbosity = (payload.get('verbosity') or '').strip() or None

    if reasoning_effort and reasoning_effort not in VALID_REASONING_EFFORTS:
        return JsonResponse({'error': '推理强度不合法。'}, status=400)

    if verbosity and verbosity not in VALID_VERBOSITY:
        return JsonResponse({'error': '详细程度不合法。'}, status=400)

    try:
        response_data = create_chat_response(
            message,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
        )
    except OpenAIConfigError as exc:
        return JsonResponse({'error': str(exc)}, status=500)
    except OpenAIRequestError as exc:
        return JsonResponse({'error': str(exc)}, status=502)

    return JsonResponse(response_data)
