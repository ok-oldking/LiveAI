# 开发指南 / Contributing guide

LiveAI 使用 Python、PySide6 和 Playwright。应用源码位于
[`src/live_gpt`](https://github.com/ok-oldking/LiveAI/tree/master/src/live_gpt)，
测试位于 [`tests`](https://github.com/ok-oldking/LiveAI/tree/master/tests)。

使用说明：[简体中文](../zh-CN/index.md) · [English](../en/index.md)。

## 开发环境 / Development setup

使用 Windows 和 Python 3.12 或更高版本，打包工作流使用 Python 3.12。
以下命令从仓库根目录执行，建议直接使用本地虚拟环境解释器。

Use Windows and Python 3.12 or later. Release builds use Python 3.12.
Run the following commands from the repository root in PowerShell.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
.\.venv\Scripts\python.exe -m live_gpt
```

## 测试 / Testing

根据修改范围运行相关测试，并在提交说明中记录结果。完整测试命令：

Run tests relevant to the change and include the results in your pull request.
To run the full suite:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

运行打包验证 / Validate release packaging:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_packaging.py -v
```

运行时依赖及其间接依赖固定在
[`requirements.txt`](https://github.com/ok-oldking/LiveAI/blob/master/requirements.txt)。
修改 [`pyproject.toml`](https://github.com/ok-oldking/LiveAI/blob/master/pyproject.toml)
中的运行依赖时，同步更新版本锁定并重新验证。文档依赖通过独立的 `docs` 可选依赖组管理，不加入发布运行环境。

Runtime and transitive dependencies are pinned in `requirements.txt`. Update those
pins when changing runtime dependencies in `pyproject.toml`, then rerun validation.
Documentation dependencies are isolated in the `docs` extra.

## Windows 发布 / Windows releases

[Windows Release 工作流](https://github.com/ok-oldking/LiveAI/blob/master/.github/workflows/build.yml)
会验证部署包，将 [`deploy.txt`](https://github.com/ok-oldking/LiveAI/blob/master/deploy.txt)
列出的文件同步到两个更新仓库，再生成中国版、国际版、在线安装包和便携启动器。
启动器配置见 [`pyappify.yml`](https://github.com/ok-oldking/LiveAI/blob/master/pyappify.yml)。
应用从 `src` 直接运行，安装包和启动器不签名。

The Windows Release workflow validates the deployed application, synchronizes the
runtime files to both update repositories, and builds unsigned China and Global
installers, an online installer, and a portable launcher. Source updates run directly
from `src`; launcher profiles are defined in `pyappify.yml`.

在源码仓库中配置以下 Actions secrets / Configure these Actions secrets in the source repository:

| Secret | 用途 / Purpose |
| --- | --- |
| `CNB_USERNAME` | 可访问更新仓库的 CNB Git HTTPS 用户名 / CNB Git HTTPS username |
| `CNB_TOKEN` | 向 `cnb.cool/ok-oldking/live-gpt-update` 推送分支和标签 / Push branches and tags to the CNB update repository |
| `UPDATE_REPO_SECRET` | 向 GitHub 的 `ok-oldking/live-gpt-update` 推送分支和标签 / Push branches and tags to the GitHub update repository |

推送 `v0.1.0` 这样的版本标签可构建并发布，`v0.1.0-beta.1` 这样的标签会发布预览版。
也可手动指定已有标签运行工作流。构建产物上传到 Actions 并附加到对应 GitHub Release。

Push a version tag such as `v0.1.0` to publish a release, or `v0.1.0-beta.1` for a
prerelease. The workflow also accepts an existing tag for manual runs. Outputs are
uploaded as workflow artifacts and attached to the corresponding GitHub release.

## Documentation website

### 本地预览 / Local preview

文档使用 MkDocs Material，目录结构与 `ok-wuthering-waves` 一致：
`docs/zh-CN/` 和 `docs/en/` 存放语言版本，`docs/development/` 存放开发说明，
`docs/stylesheets/` 存放站点样式。默认 README、首页和站点界面使用简体中文。

The website uses MkDocs Material, with localized guides in `docs/zh-CN/` and
`docs/en/`, shared development documentation in `docs/development/`, and styles in
`docs/stylesheets/`. Chinese is the default README and site language.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[docs]"
.\.venv\Scripts\python.exe -m mkdocs serve
```

打开 / Open <http://127.0.0.1:8000/>.

### 生成静态站点 / Build static HTML

```powershell
.\.venv\Scripts\python.exe -m mkdocs build --strict
```

输出位于已被 Git 忽略的 `site/`。请同步维护根目录中文 README 和两个语言的使用指南，
并在提交前运行严格构建，检查页面、导航和锚点链接。

Output is written to the ignored `site/` directory. Keep the root Chinese README
and both localized guides in sync, and run a strict build to check navigation and links.

### 发布到 GitHub Pages / Deploy to GitHub Pages

1. 打开仓库 **Settings → Pages**，将 **Build and deployment → Source** 设为 **GitHub Actions**。
2. 将文档改动推送到 `master`，或在 `master` 上手动运行 **Documentation** 工作流。
3. 严格构建通过后，工作流会上传并发布到 `github-pages` 环境。

In repository **Settings → Pages**, select **GitHub Actions** as the publishing
source. Push documentation changes to `master`, or manually run the
**Documentation** workflow on `master`. Deployment follows a successful strict
build. Pull requests build only and do not deploy.

默认站点地址 / Default site URL: `https://ok-oldking.github.io/LiveAI/`.

部署使用内置 `GITHUB_TOKEN`，无需额外密钥。调整仓库或域名时，更新 `mkdocs.yml`
中的站点与仓库地址、语言切换路径；调整部署分支时，同步修改工作流的分支过滤和条件。

Deployment uses the built-in `GITHUB_TOKEN`, with no additional secrets. For forks
or custom domains, update the site URL, repository metadata, and language switcher
paths in `mkdocs.yml`. Update workflow filters and conditions if the deployment
branch changes.

参考 / References:
[MkDocs deployment](https://www.mkdocs.org/user-guide/deploying-your-docs/),
[Material language settings](https://squidfunk.github.io/mkdocs-material/setup/changing-the-language/),
[GitHub Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
