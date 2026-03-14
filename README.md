# Albert Notes

一个基于 Django 的个人博客网站，包含个人首页、博客列表、文章详情页和 AI 聊天实验页。

## 页面结构

### 1. 首页 `/`
- 个人简介与站点定位
- 项目入口卡片
- 最近文章预览
- Intro / FAQ 标签页
- 移动端侧栏与主题切换

### 2. 博客首页 `/blog/`
- 文章列表
- 标题 / 正文 / 作者搜索
- 阅读时长与字数统计
- 主题、字体、密度切换
- 分页导航

### 3. 文章详情 `/blog/post/<id>/`
- 文章头图风格标题卡
- 正文渲染
- 阅读进度条
- 相关文章推荐

### 4. AI 实验室 `/blog/chat/`
- OpenAI Responses API 对话页
- 推理强度选择
- 输出详细度选择
- 对话消息流展示

### 5. 接口 `/blog/api/chat/`
- 接收前端聊天请求
- 调用 OpenAI 接口返回结果

## 功能板块

- 个人主页与博客分层路由
- 响应式双栏布局
- 明暗主题切换
- 字体模式切换
- 内容密度切换
- 博客搜索
- 分页
- AI 聊天实验页
- Django Admin 后台

## 技术栈

- Django 5
- SQLite
- 原生 CSS
- 原生 JavaScript

## 本地运行

```bash
cd blogsite
python manage.py runserver
```

打开：

- 首页：`http://127.0.0.1:8000/`
- 博客：`http://127.0.0.1:8000/blog/`
- AI 聊天：`http://127.0.0.1:8000/blog/chat/`
- 后台：`http://127.0.0.1:8000/admin/`

## 测试

```bash
cd blogsite
python manage.py test
```

## 设计说明

本次改版参考了以下站点的布局节奏与视觉风格，并在 Django 项目中重新实现：

- 首页参考：[xuxiny.top](https://xuxiny.top/)
- 博客页参考：[blog.xuxiny.top](https://blog.xuxiny.top/)

前端代码、文案组织和组件结构均已按当前项目重写，没有直接复制原站资源文件。
