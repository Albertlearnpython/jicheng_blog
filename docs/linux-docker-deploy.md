# Linux Docker 部署说明

本文档面向 Linux 服务器部署当前 Django 博客项目。

## 1. 服务器准备

确认服务器满足以下条件：

- 64 位 Linux
- 已开放 `80` 端口
- 已安装 Git
- 已安装 Docker Engine
- 已安装 Docker Compose Plugin

若服务器尚未安装 Docker，可参考下列常用命令：

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker
docker --version
docker compose version
```

## 2. 拉取代码

```bash
sudo mkdir -p /opt/linuxclaw
sudo chown -R "$USER":"$USER" /opt/linuxclaw
cd /opt/linuxclaw
git clone <your-repo-url> .
```

如果服务器已经有仓库，可直接：

```bash
cd /opt/linuxclaw
git pull --ff-only
```

## 3. 配置环境变量

复制示例文件：

```bash
cd /opt/linuxclaw
cp .env.example .env
mkdir -p data
```

至少需要修改这些变量：

- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `OPENAI_API_KEY`
- `FEISHU_*` 与 `REMOTE_*`（如果对应功能要启用）

如果站点前面有 Nginx/Caddy 等 HTTPS 反向代理，再将这些变量改为生产值：

```env
DJANGO_USE_X_FORWARDED_HOST=1
DJANGO_USE_X_FORWARDED_PROTO=1
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SESSION_COOKIE_SECURE=1
DJANGO_CSRF_COOKIE_SECURE=1
DJANGO_SECURE_HSTS_SECONDS=2592000
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1
DJANGO_SECURE_HSTS_PRELOAD=1
```

如果只是临时通过服务器 IP 和 `80` 端口直接访问，以上 HTTPS 相关配置先保持 `0`，否则会被重定向到不存在的 HTTPS。

## 4. 构建并启动

```bash
cd /opt/linuxclaw
docker compose up -d --build
```

查看容器状态：

```bash
docker compose ps
docker compose logs -f web
```

## 5. 更新发布

```bash
cd /opt/linuxclaw
git pull --ff-only
docker compose up -d --build
```

## 6. 数据说明

- SQLite 数据库持久化在宿主机 `data/` 目录
- 静态资源在容器启动时自动执行 `collectstatic`
- 容器首次启动时，如果 `data/db.sqlite3` 不存在，会自动复制项目自带数据库作为初始数据

## 7. 常用排障

检查 Django 配置：

```bash
docker compose run --rm web python manage.py check
```

检查生产部署配置：

```bash
docker compose run --rm \
  -e DJANGO_DEBUG=0 \
  -e DJANGO_ALLOWED_HOSTS=example.com \
  -e DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com \
  -e DJANGO_USE_X_FORWARDED_HOST=1 \
  -e DJANGO_USE_X_FORWARDED_PROTO=1 \
  -e DJANGO_SECURE_SSL_REDIRECT=1 \
  -e DJANGO_SESSION_COOKIE_SECURE=1 \
  -e DJANGO_CSRF_COOKIE_SECURE=1 \
  -e DJANGO_SECURE_HSTS_SECONDS=2592000 \
  web python manage.py check --deploy
```

查看数据库是否成功挂载：

```bash
ls -lah data
```

进入容器：

```bash
docker compose exec web sh
```

## 8. 推荐上线方式

更稳妥的方式是：

1. Docker 只运行 Django/Gunicorn。
2. Nginx 或 Caddy 在宿主机做 HTTPS 终止与反向代理。
3. 域名解析到服务器公网 IP。

这样可以兼顾部署简单度、可维护性和 HTTPS 安全配置。
