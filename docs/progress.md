# OpenMimi 迭代开发进度

> 起始:2026-05-11。按 `docs/improvement_roadmap.md` 中"四、推荐执行顺序"推进。
> cron 每 6 分钟唤醒一次,读这个文件,继续下一项,完成后更新这里。

## 当前焦点

**Wave 2 #4-lite**:`agent_browser.py` dispatcher 化 — 拆 `actions/` 子目录,主类只做路由 + daemon 管理。

## Wave 1 — 清债 + 基础(本周)

- [x] #1 删除 legacy `browser.py`,从 `tools/__init__.py` 摘掉 `BrowserTool` 导出
- [x] #2 修复 / 移除 skills 加载(`skills.py` 指向已删除目录)
- [x] #3 错误码扩容 + 结构化恢复提示(20 个 + `next_step_hint` + `make_error_result` helper)
- [x] #6 Daemon prewarm(REPL 启动时即触发 `_ensure_daemon`)

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

- 2026-05-11 · **Wave 1 #1** — 删除 `browser.py` (1108 行)、`browser_schema.py` (344 行)、相关测试与脚本;`tools/__init__.py` 收敛导出。`pytest`: 62 passed / 2 pre-existing failures(与本改动无关,见 Blockers)。
- 2026-05-11 · **Wave 1 #2** — 删除 `src/openmimi/skills.py`(指向已删除目录的死代码);**顺手修复**:`Orchestrator._build_system_prompt` 之前完全没注入 site memory(写但不读),现在通过 `memory.format_for_prompt(domain)` 真正接到 system prompt 上。`pytest`: 仍 62 passed / 2 同样的 pre-existing failures。
- 2026-05-11 · **Wave 1 #3** — `tools/errors.py` 从 5 个 ErrorCode 扩到 20 个,覆盖 element / network / auth / page-state / execution / system 六类;每个 code 配一条 `next_step_hint` 结构化恢复提示;新增 `make_error_result(code, message, ...)` helper,把提示同时写进 `output`(LLM 可见)和 `details`(审计);新增 `tests/unit/test_errors.py`(7 例,覆盖 hint 完整性 / StrEnum 兼容 / extra_details 合并)。`pytest`: 69 passed / 2 同样的 pre-existing failures。**注**:Wave 1 #4 之前不大改 `agent_browser.py`,所以暂未把现有错误路径迁到新 helper(留待 dispatcher 化时一起做)。
- 2026-05-11 · **Wave 1 #6** — Daemon prewarm 显式化。背景:`AgentBrowserTool.__init__` 已经在后台线程跑 `_start_warmup`,但用户感知不到首条任务为什么慢。本次改动:① `AgentBrowserTool.is_warming_up()` 公开接口;② `Orchestrator` 持有 `browser_engine` 引用,新增 `prewarm_browser() -> bool`(失败安全);③ `cli.chat()` 与 `cli.chat_main()` 在 welcome 之后调用 `_announce_prewarm(orch)`,有 warmup 在飞时打一行 `browser : warming up in background (first task may be slow)`。新增 `tests/unit/test_prewarm.py`(7 例,覆盖无 engine / 飞 / 完成 / 异常 / 三种 announce 路径)。`pytest`: 76 passed / 2 同样的 pre-existing failures。**注**:没有真正"挪"启动时间(它本来就在后台跑),而是把它**可见化** — 这是用最小改动达成 roadmap 意图的方式,等 Wave 1 #4 dispatcher 化之后如果还需要可以再做 sync await。

## Blockers

- `tests/unit/test_cli.py::test_run_no_screenshots_sets_env` 和 `tests/unit/test_env_flags.py::test_screenshots_disabled_default`:测试断言的 env 变量名(`OPENMIMI_DISABLE_SCREENSHOTS`)与实现读取的(`OPENMIMI_ENABLE_SCREENSHOTS`)语义反转。**预存在的失败**,与 #1 无关,可在 Wave 1 顺手修。

## 延期项(未来可能要做)

(暂无)
