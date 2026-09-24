# token-tracker 项目约定

## 目标

维护 `stormzhang/token-tracker` 的个人 fork，优先排查并修复 Codex 更新后的状态栏兼容问题。`origin` 指向 `wandaifa/token-tracker`，`upstream` 指向原项目；同步上游前先检查分支差异。

## 技术栈与版本

- Python 3.11+；依赖和入口见 `pyproject.toml`，锁文件为 `uv.lock`。
- 版本唯一事实源是 `pyproject.toml` 的 `[project].version`；用户可见版本由 `tt --version` 显示，README 和发布产物须与之对应。
- 本地分析不改用户级安装或配置。改运行行为且验证通过后，按项目版本规则升版。

## 目录结构

- `src/token_tracker/` 放运行代码；`templates/` 放由 setup 烘焙的状态栏脚本，安装逻辑在 `hooks.py` 与 `sidebar_install.py`。
- `tests/` 放有意义的回归测试；`assets/` 放 README 使用的静态图片。
- 仓库根目录仅放项目配置、规则、说明和进度文档。临时验证产物使用系统临时目录，不提交；任务结束清理前遵守删除确认规则。

## 验证

- 在项目自己的虚拟环境安装 `[dev]` 依赖后，定向测试：`.venv/bin/python -m pytest tests/test_hooks.py tests/test_sidebar.py tests/test_sidebar_app.py`
- 全量测试：`.venv/bin/python -m pytest`
- 静态检查：`.venv/bin/ruff check .`、`.venv/bin/mypy src/token_tracker`、`git diff --check`
- Hook 展示兼容性不能只凭单测判断，需用目标 Codex 版本真实运行并核对终端实际显示；未实测不得标记修复完成。

## 进度

`ROADMAP.md` 记录本 fork 的当前阶段、已完成、进行中、待办、阻塞和最近验证。上游历史记录保留，但本 fork 的新结论须标明验证范围与日期。
