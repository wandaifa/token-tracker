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

## 已完成的计费同步范围（2026-11-16）

- 基于实际已安装的 `150e3f1`／`0.7.0` 建立隔离候选，仅同步官方 `990ef700` 的 `analyzer/cost.py`、`ui/format.py` 及计费回归测试。
- 保留现有 Codex Hook、iTerm2 Profile、tmux 底栏和状态栏模板；不混入未安装的 `0.8.0` 候选或 Windows 安装修复。
- 先用官方新增用例复现旧计费问题，再验证全量测试、静态检查及彩色渲染；验收通过后修订号升至 `0.7.1`。
- 用户级安装前保存当前版本的可回退副本；不运行 `tt setup`，不改真实 Hook、主题、终端 Profile 或 shell 配置。

## 移除 tmux 支持（2026-11-16 用户确认）

- 本轮覆盖固定底栏、sidebar 分屏与点击跳转、三 Agent 终端映射、对应模板／测试／Skill／说明中的 tmux 支持；普通 iTerm2 彩色摘要及原生分屏、Ghostty 分屏、计费逻辑保持。
- 旧计费同步中的「保留 tmux」仅为当时范围，现由本节替代。历史进度记录保留，不将旧记录当作当前能力。
- 验收后版本使用 `0.9.0`，避免与另一未安装的 `0.8.0` 候选重号；本轮为跨模块能力移除，不增加依赖。
- 先备份既有安装和待更新的托管脚本／Skill，再安装验收包并定向更新托管产物；不执行全量 setup，不改 Hook 注册、主题、终端 Profile、shell 配置或会话数据。
- 安装后逐文件与包核对、检查运行源码与已安装脚本／Skill 无 tmux 引用，并验证真实会话彩色渲染；不关闭现有会话，不卸载未发现的系统软件。
