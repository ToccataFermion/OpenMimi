# OpenMimi 提升路线图

> 起源:2026-05-10 对当时代码的诊断。
> 文档分两层:**原始分析**保留作为参考(包括用户暂不认可的部分);**执行优先级**反映 2026-05-11 用户裁定的实际工作清单。

---

## 一、现状诊断

### A. 架构层面的根本性短板

| # | 问题 | 影响 | 证据 |
|---|------|------|------|
| 1 | 没有 Planner / Reflect 层 | sampling loop 是单层 ReAct,任务一长就漂移、忘目标、循环卡死。`max_turns=30` 是唯一兜底 | `loop.py` 整个 loop 只有 LLM ↔ tool 两端,没有 plan / act / verify / replan 阶段 |
| 2 | Tool 设计是 god-tool + 薄 facade | `agent_browser.py` 3756 行单文件,40+ action 的分支判断挤在一起,迭代成本高 | `browser_navigate/interact/extract/advanced` 只是 schema 切片,底层仍走同一个 `__call__(engine)` |
| 3 | Memory 单薄 | 仅 site-level summary;同一域名才命中,跨站经验无法迁移;**会话结束才写一次**,会话内不可复用 | `orchestrator._save_session_memory` 只在 `run_task` finally 里调,且仅写 `known_refs/tips/failure_patterns/success_paths` 四个 key |
| 4 | 没有跨 session 的细粒度检索 | 无法基于"相似情境"召回过去步骤,只有粗粒度的 site summary | `memory/sqlite_store.py` + `site_store.py` 都是 key-value 文件 |
| 5 | 错误反馈极粗 | 只有 5 个 ErrorCode,大量真实失败被压成 `TOOL_INTERNAL_ERROR`,LLM 拿不到结构化提示 | `errors.py:21` 只到 `CAPTCHA_DETECTED` |
| 6 | 无验证/自评估闭环 | LLM 自己宣布"任务完成"就停;没人检查是否真完成 | `loop.py:181` `if not tool_use_blocks: return messages` |
| 7 | Context 压缩太粗暴 | `_compress_old_tool_results` 直接截断到 400 字符,丢失结构化中间结论 | `loop.py:512` `text[:truncate_len] + suffix` |

### B. 工程债

| # | 问题 | 备注 |
|---|------|------|
| 8 | `browser.py`(1108 行)是 legacy 死代码,仍在 `__init__.py` 暴露 | orchestrator 只用 `AgentBrowserTool`,但 `BrowserTool` 仍被 export |
| 9 | `skills/` 目录已被 commit `ec786b5` 删除,但 `skills.py:11` 仍指向它 | 任何 domain skill 注入都静默失败 |
| 10 | CAPTCHA 关键词、解题步骤硬编码在 `agent_browser.py` 中 | 不是 plugin,扩展新验证方式必须改主文件 |
| 11 | Browser daemon 冷启动 2-5 分钟仅靠 `tout = max(tout, 600.0)` 兜底,无 prewarm/池化 | `loop.py:222` |
| 12 | 没有 token budget,只有 turn budget | 一个 image-heavy 会话很容易在 5-10 turn 就 100k tokens |
| 13 | 没有 sub-agent / 隔离 | CAPTCHA、深度搜索这种"一次性、高消耗"子任务会污染主 context |
| 14 | `JsonlAuditLogger` 写盘但没有内置 replay viewer | 调试只能 `cat data/audit/*.jsonl` |
| 15 | system prompt 是字符串字面量 | 无法按任务类型/能力等级动态选择 |

---

## 二、提升路线图

按"投入产出比 × 是否阻塞下游"排序,分三层。

### 短期 · 1-2 周(技术债清理 + 基础修补)

**目的:让现有架构跑得更稳、为中期升级让路。**

1. **删除 legacy 代码**
   - 移除 `browser.py`、从 `tools/__init__.py` 摘掉 `BrowserTool` 导出。1108 行死代码 = 维护成本。
2. **修复 / 重建 skills 加载**
   - 要么在 `skills.py:11` 改成警告 + 软失败,要么把已删除的 skills 用站点 memory 替代,把 `skills.py` 整个移除。当前是隐性 dead path。
3. **错误码扩容到 ~20 个**(下面要用)
   - 至少补:`ELEMENT_NOT_VISIBLE`、`ELEMENT_DETACHED`、`NETWORK_ERROR`、`AUTH_REQUIRED`、`RATE_LIMITED`、`PERMISSION_DENIED`、`UNEXPECTED_DIALOG`、`SESSION_EXPIRED`、`SCRIPT_ERROR`。
   - 每个错误带"建议下一步"字段,LLM 直接看到结构化恢复提示,不用每次都重新摸索。
4. **拆 `agent_browser.py`**
   - 按 action 族拆成 `nav.py / interact.py / extract.py / network.py / captcha.py / cdp.py`,每文件 < 600 行。
   - god `__call__` 改 dispatcher,每族一个 handler。
   - 这是中期 multi-agent 化的前置工程。
5. **Token budget + 智能压缩**
   - `loop.py` 加 `max_input_tokens` 总闸;超阈值时调用 LLM 做"结构化总结"(保留:URL 历史、登录态、抓到的关键字段),而不是机械截断。
6. **Daemon prewarm**
   - REPL 启动时就 `await tools.browser._ensure_daemon()`,把 5 min 冷启动从首条任务里挪出去。

### 中期 · 1-2 月(架构升级,这是"智能跃迁"的核心)

**目的:从单层 ReAct 进化到 plan-act-verify-reflect 多 agent 体系。**

7. **引入 Planner / Executor / Verifier 三角**
   ```
   User Task
     ├─ Planner LLM (一次性,廉价模型)→ 产出 step list (JSON)
     ├─ Executor (现有 sampling_loop) → 按 step 执行
     └─ Verifier LLM(每 N step)→ 检查目标达成度,触发 replan
   ```
   - Planner 输出格式如 `[{step, success_criteria, allowed_tools, budget}]`
   - Verifier 拿 success_criteria + 当前页面状态对照,返回 `done/partial/failed/replan`
   - 这一步直接决定能否做"打开淘宝→搜索→筛选→对比 3 件商品→输出表格"这种长链任务。
8. **Sub-agent 隔离**(把"一次性高消耗子任务"剥离出主 context)
   - 用 `Agent` 模式 spawn 专用子 agent:CAPTCHA solver、表单填写、深度搜索、长 PDF 阅读。
   - 子 agent 只把"最终结果"返主 context,不把 50 张验证码截图带回来。
   - 实现上加一个 `sub_agent` 工具:`spawn(task, tools_subset, max_turns) → result`。
9. **Memory v2:三层结构**
   - **Episodic**(每个 step 一条):action + observation + outcome,落盘。
   - **Semantic**(每域一条):当前的 site memory 升级为"结构化知识图":URL 模式、表单字段映射、登录流程图。
   - **Procedural**(技能):跨站可复用的"脚本片段",如"搜索框→输入→Enter→等结果"模板。
   - **检索机制(原方案 vs 用户决定)**:
     - 原方案:embedding + FAISS/Chroma 向量库
     - **用户决定改为:Claude Code 风格的 grep + read 文件系统**(详见第三节)
10. **能力分层 system prompt**
    - 把当前一坨 prompt 拆成 `core / browser / desktop / captcha / forms` 模块,按当前任务激活的工具集动态拼装,降低 baseline token。
11. **Replay/Trace UI**
    - 简单 webui:用 `data/audit/*.jsonl` 渲染 timeline + screenshot diff。每个 step 可导出为 episodic memory 的"金标准样例"。
    - 这是后面 fine-tune / RAG 选样的数据基础。
12. **结构化 ToolResult**
    - 当前 `ToolResult.output` 是字符串。改成可选 `structured: dict`(如 `extract` 返回 `{rows: [...]}`、`network_log` 返回 `{requests: [...]}`),让 LLM 直接读 JSON 而不是 parse 文本。

### 长期 · 3-6 月(向通用 agent 演进)

**目的:从"网页自动化工具"变成"能学习、能泛化、能自我改进的通用 agent"。**

13. **Skill 学习闭环(self-improvement)** — 成功任务自动抽象成 skill 模板,失败案例进 anti-pattern 库。
14. **Skill marketplace / 文件化 skill** — 仿 Claude Code skill 格式:`skills/<skill_name>/SKILL.md` + 脚本 + sample。
15. **多模态视觉 grounding** — 把 `visual_locate` 从模板匹配换成 vision LLM 直接给坐标(SeeClick / OmniParser 风格)。
16. **跨应用 agent** — `ComputerTool` 提到和 `BrowserTool` 同级语义层,Planner 编排"浏览器→Excel→邮件"。
17. **本地小模型分流** — 廉价决策走 7B-13B,大决策走 Sonnet/Opus。
18. **风险/权限分级** — `danger_level` 字段;付款/转账强制 human-in-the-loop。
19. **基准测试 harness** — 自建 e2e 任务集(WebArena / Mind2Web 风格)纳入 CI。

---

## 三、用户裁定的执行优先级(2026-05-11)

### 已选(按编号,非优先级排序)

短期:**1、2、3、5、6**
中期:**7、8、9、11、12**

### 未选(暂缓)

- **#4(拆 `agent_browser.py`)** — 用户暂未排入,但属于 #7/#9 的工程前置,实操中可能需要至少做"轻量 dispatcher 化"。
- **#10(分层 system prompt)** — 可与 #7 一起做。
- **#13–#19(长期)** — 全部暂缓。

### 关键决策:Memory v2 用 grep,不用向量库

原方案 #9 提议 embedding + FAISS/Chroma。**用户裁定改用 Claude Code 风格的文件系统 + grep + read**:

**落盘格式**
- Episodic:`data/memory/episodic/<YYYY-MM>/<session_id>/<step>.json`,每 step 一行,含 action/observation/outcome/url/domain/timestamp。
- Semantic:`data/memory/sites/<domain>.md`(把现有 JSON 升级为可读 markdown,人和 LLM 都能改)。
- Procedural:`data/memory/skills/<skill>.md`(skill 模板,markdown 格式)。

**检索工具(暴露给 LLM)**
- `memory_grep(pattern, scope=episodic|semantic|skills, glob=...)` — 仿 ripgrep。
- `memory_read(path)` — 读单条 episodic / 单个 skill。
- `memory_write(path, content)` — 让 LLM 主动写入 semantic / skill。
- `memory_list(scope, filter)` — 列文件,支持按 domain / 时间过滤。

**优势**
- 零依赖、可读、可审计,人和 LLM 都能直接 `cat` / `grep` 调试。
- 调试体验好于黑盒向量库。
- 大模型对 grep 的理解远好于"向量相似度阈值"——失败模式可读。
- 只有当 LLM 检索精度被实测证明不足时,才考虑加向量层。

### 暂未采纳(供未来参考)

- 长期路线图(13–19)整段保留作未来候选。
- 所有非用户已选项不删除,以防需要回头采纳。

---

## 四、推荐的最小起点(在已选项内重排)

如果按"解锁度 × 工程量"在已选项里挑前三件:

1. **#3 错误码扩容 + 结构化恢复提示** — 1 天工作量,LLM 立刻变聪明。
2. **#7 Planner / Verifier 双 LLM 包一层** — 1-2 周,复杂任务能力直接翻倍,这是从"做一步走一步"到"会规划"的质变。
3. **#9 Memory v2(grep 版)** — 2-3 周,跨 session 经验沉淀的基础;先做 episodic JSONL + 工具,再补 semantic / procedural。

#1、#2、#5、#6、#12 都是局部小改,可穿插进上述三件大事中间做。
#8、#11 在 #7 落地后再做更顺。
