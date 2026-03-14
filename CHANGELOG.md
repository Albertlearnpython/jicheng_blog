# Changelog

## 2026-03-14

### `4e543dd` Personalize profile and animated site styling
- 将站点个人信息替换为孙伯符 / Noah Brooks 的真实资料。
- 博客名称更新为“孙伯符的博客”。
- 首页加入头像展示、联系方式卡片、学校与城市信息。
- 首页、博客页、文章详情页、AI 实验室页面增加渐变文字、发光变色和强调语句样式。
- 新增站点头像资源文件 `blogsite/blog/static/blog/noah-avatar.svg`。
- 同步更新 README 与测试断言。

### `5390da1` Improve layout spacing and UI sound feedback
- 调整全站容器宽度、卡片留白、区块间距与移动端布局，缓解页面拥挤问题。
- 为主题切换、标签切换、按钮点击、侧栏开合等交互加入 Web Audio 合成音效。
- 新增音效开关按钮，允许手动开启或关闭交互声音。

### `bf6b55c` Redesign homepage and blog experience
- 将根路径 `/` 重做为个人首页。
- 将博客主列表迁移到 `/blog/`。
- 保留并重构文章详情页 `/blog/post/<id>/` 与 AI 聊天页 `/blog/chat/`。
- 新建统一站点样式与脚本文件，按参考站点风格重写前端结构。
- 更新 README，补充站点功能板块说明。
