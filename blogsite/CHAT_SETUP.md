# Mobile Chat Setup

这个项目现在已经包含两个新增路由：

- `/chat/`：手机浏览器访问的聊天页面
- `/api/chat/`：页面调用的后端接口

先在 Linux 服务器上设置环境变量：

```bash
export OPENAI_API_KEY="你的_api_key"
export OPENAI_API_URL="https://api.openai.com/v1/responses"
export OPENAI_MODEL="gpt-5.4"
export OPENAI_REASONING_EFFORT="medium"
export OPENAI_TEXT_VERBOSITY="medium"
export DJANGO_ALLOWED_HOSTS="你的域名,你的服务器IP"
export DJANGO_DEBUG="false"
```

如果你接的是自己的兼容网关，把 `OPENAI_API_URL` 改成你的 `/v1/responses` 地址即可。

开发环境直接启动：

```bash
python manage.py runserver 0.0.0.0:8000
```

然后在手机浏览器里打开：

```text
http://你的服务器IP:8000/chat/
```

如果你要长期运行，推荐再配一个 `gunicorn + nginx` 或 `systemd` 服务。
