# OpenMimi 迭代开发进度

> 起始:2026-05-11。按 `docs/improvement_roadmap.md` 中"四、推荐执行顺序"推进。
> cron 每 6 分钟唤醒一次,读这个文件,继续下一项,完成后更新这里。

## 当前焦点

**Wave 1 #1**:删除 legacy `browser.py`(1108 行死代码)。

## Wave 1 — 清债 + 基础(本周)

- [ ] #1 删除 legacy `browser.py`,从 `tools/__init__.py` 摘掉 `BrowserTool` 导出
- [ ] #2 修复 / 移除 skills 加载(`skills.py` 指向已删除目录)
- [ ] #3 错误码扩容 + 结构化恢复提示(~20 个,带 `next_step_hint` 字段)
- [ ] #6 Daemon prewarm(REPL 启动时即触发 `_ensure_daemon`)

## Wave 2 — 工程解耦(下周)

- [ ] #4-lite `agent_browser.py` dispatcher 化(拆 `actions/` 子目录,主类只做路由 + daemon 管理)
- [ ] #12 结构化 ToolResult(新增可选 `structured: dict` 字段)

## Wave 3 — 智能跃迁(2-3 周)

- [ ] #7 Planner / Executor / Verifier 三角
- [ ] #5 Token budget + 智能 LLM 压缩(替代 400 字符截断)

## Wave 4 — 跨 session 记忆(3-4 周)

- [ ] #9 Memory v2(grep 版):episodic / semantic / procedural 三层 + `memory_grep/read/write/list` 工具

## Wave 5 — 隔离 + 调试

- [ ] #8 Sub-agent 隔离(`spawn(task, tools_subset, max_turns) → result`)
- [ ] #11 轻量 Replay 脚本(`python -m openmimi.replay <session_id>` → 单页 HTML)

## 工作守则(给未来唤醒的我)

1. 每次唤醒先 `git status` + 读这个文件,确认从哪里继续。
2. 一次只做一小步(目标:每次唤醒能产出 1 个 commit,或在长项目中推进一个明确小阶段)。
3. 完成后必须:
   - 跑现有测试 `pytest tests/` 确认没破坏
   - 创建 git commit(信息清晰、作用域窄)
   - 在本文件上方标 `[x]`,把"当前焦点"指到下一项
4. 不能自主决策的问题(API/架构方向不明、需要外部账号等)→ 写入 `docs/blockers.md`,跳过该项继续下一个。
5. 不要扩张范围:遇到看似相关但不在已选项内的事项,记到本文件末尾的"延期项"下,不要现场改。
6. 失败/破坏性操作前(`rm -rf` / `git reset --hard` / 强推)→ 一律先停手记录到 blockers,等用户回来。

## 已完成

(暂无)

## Blockers

(暂无)

## 延期项(未来可能要做)

(暂无)
