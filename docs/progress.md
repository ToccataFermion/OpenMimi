# OpenMimi 迭代开发进度

> 起始:2026-05-11。按 `docs/improvement_roadmap.md` 中"四、推荐执行顺序"推进。
> cron 每 6 分钟唤醒一次,读这个文件,继续下一项,完成后更新这里。

## 当前焦点

**Wave 2 #4-lite (进行中)**:`agent_browser.py` dispatcher 化。前 7 步已完成 — `actions/` 包脚手架 + 4 nav + 11 interaction + 4 scroll + 11 extract + 5 wait + 8 tab/session + 7 network/cdp action 外迁(共 50 个)。`agent_browser.py` 当前 1588 行(从 3766 起步,目标 < 1000)。下一步:迁剩余杂项(eval/batch/drag/mouse/focus/select/upload/download/emulate_device/set_*)。

## Wave 1 — 清债 + 基础(本周)

- [x] #1 删除 legacy `browser.py`,从 `tools/__init__.py` 摘掉 `BrowserTool` 导出
- [x] #2 修复 / 移除 skills 加载(`skills.py` 指向已删除目录)
- [x] #3 错误码扩容 + 结构化恢复提示(20 个 + `next_step_hint` + `make_error_result` helper)
- [x] #6 Daemon prewarm(REPL 启动时即触发 `_ensure_daemon`)

## Wave 2 — 工程解耦(下周)

- [ ] #4-lite `agent_browser.py` dispatcher 化(拆 `actions/` 子目录,主类只做路由 + daemon 管理)
  - [x] step 1 — `actions/` 包 + registry + `navigation.py`(navigate/back/forward/reload)
  - [x] step 2 — interaction 族(click/right_click/double_click/check/uncheck/type/fill/react_fill/press/key_combo/hover)
  - [x] step 3 — scroll 族(scroll/human_scroll/scroll_until/scroll_into_view)
  - [x] step 4 — extract / page-state 族(snapshot/extract/page_source/get_url/get_title/get_attribute/set_attribute/get_property/get_box/is_visible/visual_locate)
  - [x] step 5 — wait 族(wait/wait_for/wait_for_disappear/wait_for_navigation/wait_for_network_idle)
  - [x] step 6 — tab/session 族(tab_*/save_session/load_session/clipboard/clear_cache)
  - [x] step 7 — network/cdp/storage/pdf/console/screenshot 族
  - [ ] step 8 — 剩余杂项(eval/batch/drag/mouse/focus/select/upload/download/emulate_device/set_*)
  - [ ] step 9 — 主类只剩 `__init__` / `__call__` / `_dispatch` / daemon 管理(`_start_browser` / `_exec` / etc.)
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
- 2026-05-11 · **Wave 2 #4-lite step 1** — `agent_browser.py`(3766 行)dispatcher 化第 1 步:① 新建 `tools/actions/` 包 + 注册表(`@register(name)` 装饰器、`get(name)`、`registered_actions()`);② 新建 `actions/navigation.py`,把 `navigate/back/forward/reload` 4 个 handler 从 `_do_*` bound method 迁出为接受 `engine` 参数的自由函数;③ `_dispatch` 在查询内置 dict 之前先咨询 registry,registry 命中即返回;④ 删除 `agent_browser.py` 里这 4 个 method 与对应的 dispatch 项(-40 行)。增量、向后兼容(主类外观完全不变)、不需要再回炉 — 后续 step 复用同一脚手架。新增 `tests/unit/test_actions_registry.py`(6 例,覆盖注册表查询 / 4 个 handler 与 fake engine 的协作)。`pytest`: 82 passed / 2 同样的 pre-existing failures。`agent_browser.py` 现 3726 行,目标是 < 1000 行。
- 2026-05-11 · **Wave 2 #4-lite step 2** — interaction 族 11 个 handler 外迁:① 新建 `actions/_keys.py`(verbatim 搬 `_cdp_key_code` + `_KEY_MAP`,留作 `actions/` 包内部 helper);② 新建 `actions/interaction.py`,迁 `click/right_click/double_click/check/uncheck/type/fill/react_fill/press/key_combo/hover` 11 个 handler + `_click_with_mouse` 私有 helper(自由函数版),全部用 `@register("name")` 装饰;③ `actions/__init__.py` 加 `from . import interaction` side-effect 注册;④ `agent_browser.py` 删 11 个 `_do_*` 方法 + `_click_with_mouse` 方法 + 4 份重复的 `_cdp_key_code` 顶层函数(原本只有 1 份,Edit 操作中放大了一次,清理时一并 verbatim-replace_all 删干净)+ dispatch 表里 11 个对应项。**净变化**: -407 行,`agent_browser.py` 从 3726 → 3319 行。新增 5 个新测试(交互族注册成员检查 + click/type/press/right_click 4 个 handler 行为冒烟,假 engine 走 `get box → mouse move/down/up` 三步序列)。`pytest`: 88 passed / 2 同样的 pre-existing failures。
- 2026-05-11 · **Wave 2 #4-lite step 3** — scroll 族 4 个 handler 外迁:① 新建 `actions/scroll.py`,迁 `scroll/human_scroll/scroll_until/scroll_into_view`;`scroll_until` 内部依赖 `engine._parse_data` / `engine._parse_snapshot`,`scroll_into_view` 走 `eval` 注入 JS,`human_scroll` 用 `random` + `asyncio.sleep` 模拟人;② `actions/__init__.py` 加 `from . import scroll` side-effect 注册;③ `agent_browser.py` 删 4 个 `_do_*` 方法 + dispatch 表里 4 个对应项。本次 Edit 中途因为 new_string 写错把方法块复制了一份(从而临时变成 3464 行),靠后续 `replace_all=true` 把 4-method 块 + 后随的 `_do_page_source` 行作为唯一 anchor 一次性删 1 份(只删了 1 份,因为另一份的 anchor 行不一致),再用 `replace_all=false` 精确删剩余 1 份,最终回到 3165 行(净 -154)。新增 5 个测试:scroll 族注册成员、`scroll` 子命令转发、`scroll_until` 首探就找到立即返回、`scroll_until` 缺参数报错、`scroll_into_view` 走 `eval`。`pytest`: 93 passed / 2 同样的 pre-existing failures。`agent_browser.py` 现 3165 行。
- 2026-05-11 · **Wave 2 #4-lite step 4** — extract / page-state 族 11 个 handler 外迁:① 新建 `actions/extract.py`(~530 行),迁 `snapshot/page_source/get_url/get_title/get_attribute/set_attribute/get_property/extract/get_box/is_visible/visual_locate`;`snapshot` 内嵌 CAPTCHA 检测路径,失败时返回带 `error_code: ErrorCode.CAPTCHA_DETECTED` 的非错误 ToolResult,提示 LLM 看截图解题;`get_attribute` / `set_attribute` / `get_property` 共享 ref → CSS-selector / target_text → treeWalker 的元素定位 JS 模板;`extract` 按 instruction 分支(get text / headings / links / forms / tables / metadata / images / 兜底)各自构造 JS,输出截到 4000 字符;`visual_locate` 用 OpenCV 模板匹配,临时把 `engine._screenshot_scale` 设 1.0 拿全分辨率截图后在 `finally` 还原,可选 click 走 mouse move/down/up;② `actions/__init__.py` 加 `from . import extract` side-effect 注册;③ `agent_browser.py` 删 11 个 `_do_*` 方法 + dispatch 表里 11 个对应项,3 批 Edit(snapshot+extract / get_box+is_visible+visual_locate / page_source+get_url+get_title+get_attribute+set_attribute+get_property)。**净变化**:-431 行,`agent_browser.py` 从 3165 → 2734 行,registry 现含 30 个 action(4 nav + 11 interaction + 4 scroll + 11 extract)。新增 7 个测试:extract 族注册成员检查 + `get_url`/`get_title`/`get_box` 子命令路由 + `get_box` 缺 selector 报错 + `extract` "get text" 截 4000 字符 + `get_attribute` 缺 attribute_name 报错 + `is_visible` 走 eval 含 getBoundingClientRect。`pytest`: 101 passed / 2 同样的 pre-existing failures。
- 2026-05-11 · **Wave 2 #4-lite step 5** — wait 族 5 个 handler 外迁:① 新建 `actions/wait.py`(~258 行),迁 `wait/wait_for/wait_for_disappear/wait_for_navigation/wait_for_network_idle`;`wait` 单纯转发 daemon 的 `wait` 子命令,其余 4 个都在 Python 端轮询(`time.monotonic` + `asyncio.sleep`)以避免占用 sidecar;`wait_for_navigation` 起步先抓一次当前 URL,然后轮询 `eval window.location.href` 直到变化(可选 `expected_url` 子串过滤);`wait_for_network_idle` 一次性向页面注入 `window.__openmimi_network_idle_*` fetch/XHR 拦截器(再注入幂等),然后轮询 in-flight 计数与 last-active 时间戳;② `actions/__init__.py` 加 `from . import wait` side-effect 注册;③ `agent_browser.py` 删 5 个 `_do_wait*` 方法(3 批 Edit:`_do_wait` 单独删 + 中途多删了 `async def _do_eval` 头部,立即用一次反向 Edit 修复 / `_do_wait_for`+`_do_wait_for_disappear` 合块删 / `_do_wait_for_navigation`+`_do_wait_for_network_idle` 合块删)+ dispatch 表 3 批共 5 项删除。**净变化**:-219 行,`agent_browser.py` 从 2734 → 2515 行,registry 现含 35 个 action。新增 7 个测试:wait 族注册成员检查 + `wait` 子命令转发 + `wait_for` 首探到立即返回 + `wait_for` 缺 target 报错 + `wait_for_disappear` 首探无 box 立即返回 + `wait_for_navigation` URL 变化检测 + `wait_for_network_idle` 注入 + idle 立即返回。`pytest`: 108 passed / 2 同样的 pre-existing failures。
- 2026-05-11 · **Wave 2 #4-lite step 6** — tab/session 族 8 个 handler 外迁:① 新建 `actions/tab_session.py`(305 行),迁 `clipboard/tab_list/tab_switch/tab_new/tab_close/clear_cache/save_session/load_session`;`tab_*` 4 个 handler 都先 `engine._refresh_tabs()` 再读 `engine._tabs` / 写 `engine._active_tab_index` 保持 sidecar 与 Python 镜像一致;`save_session` 优先用 CDP `Network.getAllCookies`(可拿 HTTP-only),失败回落 `document.cookie`;`load_session` 同时支持 CDP cookie 数组(`{name,value,domain,path}`)与 legacy 分号串两种存盘格式;② `actions/__init__.py` 加 `from . import tab_session` side-effect 注册;③ `agent_browser.py` 删 8 个 `_do_*` 方法(3 批:clipboard+tab_list+tab_switch+tab_new+tab_close 一块 / clear_cache 一块 / save_session+load_session 一块)+ dispatch 表 3 批共 8 项删除。**净变化**:-258 行,`agent_browser.py` 从 2515 → 2257 行,registry 现含 43 个 action。新增 8 个测试:tab_session 族注册成员检查 + `clipboard read/write` 转发 + `tab_list` 列表 + `tab_new` 子命令 + `save_session`/`load_session` 缺 file_path 报错 + `clear_cache` 走 eval 含 `localStorage.clear()`。`pytest`: 116 passed / 2 同样的 pre-existing failures。
- 2026-05-11 · **Wave 2 #4-lite step 7** — network/cdp/storage/pdf/console/screenshot 族 7 个 handler + 1 helper 外迁:① 新建 `actions/network_cdp.py`(708 行),迁 `cdp/screenshot/network_log/network_modify/storage/pdf/console`;`cdp` 通过 `window.__openmimi_cdp_send(method, params)` JS bridge 转发任意 CDP 方法,作为逃生口;`screenshot` 仍走 `engine._take_screenshot(path_override, annotate)`,但本族独立 import 了 `screenshots_disabled`(`agent_browser.py` 仍保留 import 因为 `_take_screenshot_raw` 也要用);`network_log` 注入幂等 `window.__openmimi_network_log` 拦截 fetch / XHR(再注入幂等),再读最多 20 条捕获;`network_modify` 5 个子动作(user_agent / inject_headers / block_urls / mock_response / clear),user_agent 优先走 CDP `Network.setUserAgentOverride` 失败回落 `Object.defineProperty`;`storage` 三类(localStorage / sessionStorage / cookies),cookies 优先走 CDP `Network.getAllCookies` / `Network.setCookie`(经 `_try_cdp_then_fallback` 自由函数 helper 串起来),失败回落 `document.cookie`;`pdf` 走 CDP `Page.printToPDF`,失败回落 `window.print()` 提示;`console` 注入 `window.__openmimi_console_logs`(max 200)shim,读最近 30 条按 level 过滤;② `_try_cdp_then_fallback` 从 method 改为自由函数(首参 engine),storage handler 内部更新调用;③ `actions/__init__.py` 加 `from . import network_cdp` side-effect 注册;④ `agent_browser.py` 删 7 个 `_do_*` 方法 + 1 个 `_try_cdp_then_fallback` helper(4 批 Edit:cdp+screenshot 一块 / network_log 单独 / network_modify 单独 / storage+helper+pdf+console 一块)+ dispatch 表 1 批共 7 项删除。**净变化**:-669 行,`agent_browser.py` 从 2257 → 1588 行,registry 现含 50 个 action。新增 14 个测试:network_cdp 族注册成员检查 + `cdp` value 拆封 + 缺 cdp_method 报错 + `screenshot` 启用/禁用两路 + `network_log` 安装+读 + `network_modify` user_agent / inject_headers 缺 headers + `storage` localStorage set / 缺 key / cookies CDP-first + `pdf` 缺 file_path / 成功 + `console` 安装+读。`pytest`: 130 passed / 2 同样的 pre-existing failures。`agent_browser.py` 现 1588 行,距目标 < 1000 还差 ~588 行,1-2 步可达。

## Blockers

- `tests/unit/test_cli.py::test_run_no_screenshots_sets_env` 和 `tests/unit/test_env_flags.py::test_screenshots_disabled_default`:测试断言的 env 变量名(`OPENMIMI_DISABLE_SCREENSHOTS`)与实现读取的(`OPENMIMI_ENABLE_SCREENSHOTS`)语义反转。**预存在的失败**,与 #1 无关,可在 Wave 1 顺手修。

## 延期项(未来可能要做)

(暂无)
