# 文档维护说明

本目录是 LiveAI 文档站点的 Markdown 源码，结构沿用 `ok-wuthering-waves`。
仓库默认 README 和站点首页使用简体中文，完整英文说明保留在 `en/`。

## 目录结构

| 路径 | 用途 |
| --- | --- |
| `../README.md` | GitHub 仓库默认中文说明 |
| `index.md` | 中文站点首页和语言入口 |
| `zh-CN/index.md` | 完整简体中文使用指南 |
| `en/index.md` | 完整英文使用指南 |
| `development/contributing.md` | 开发、测试、发布和 Pages 配置 |
| `stylesheets/extra.css` | 站点专用样式 |
| `../mkdocs.yml` | 站点信息、语言、导航和主题 |
| `../.github/workflows/docs.yml` | 构建检查及 GitHub Pages 部署 |

修改功能说明时，同步更新根目录 `README.md`、`zh-CN/index.md` 和 `en/index.md`。
仓库 README 使用仓库相对链接，站点指南中的源码链接使用 GitHub 地址，避免生成的网站出现断链。
此维护文件通过 `exclude_docs` 排除，不会作为站点页面发布。

## 本地预览

在仓库根目录执行；如果尚未创建虚拟环境，先运行 `py -3.12 -m venv .venv`：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[docs]"
.\.venv\Scripts\python.exe -m mkdocs serve
```

打开 <http://127.0.0.1:8000/>。保存 Markdown 后，预览会自动重新构建。

## 生成静态站点

```powershell
.\.venv\Scripts\python.exe -m mkdocs build --strict
```

输出目录为 `site/`，已在 `.gitignore` 中排除。请修改 `docs/` 源文件，不要编辑生成文件。

## GitHub Pages 部署

1. 在 GitHub 仓库中打开 **Settings → Pages**。
2. 将 **Build and deployment → Source** 设为 **GitHub Actions**。
3. 将文档及配置提交到 `master`，或在 `master` 上手动运行 **Documentation** 工作流。
4. 构建通过后，工作流上传静态站点并部署到 `github-pages` 环境。

默认站点地址为 `https://ok-oldking.github.io/LiveAI/`。
拉取请求只执行严格构建，不部署，也不申请 Pages 写权限。
工作流使用内置 `GITHUB_TOKEN`，不需要额外配置部署密钥。

如需使用其他仓库或自定义域名，请同步修改 `mkdocs.yml` 的 `site_url`、
`repo_url`、`repo_name` 和 `extra.alternate` 中的语言路径。
默认部署分支是 `master`，调整分支时还需修改工作流中的分支筛选和部署条件。

配置参考：[MkDocs](https://www.mkdocs.org/user-guide/deploying-your-docs/) ·
[Material 语言配置](https://squidfunk.github.io/mkdocs-material/setup/changing-the-language/) ·
[GitHub Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。
