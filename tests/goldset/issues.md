# Goldset issue log

Reverse-chronological. Each entry is one issue surfaced by an autonomous
goldset cycle: what broke, why, and how it was fixed (or why no fix was
applied). Lets you scan all regressions caught by the rotation without
diffing every commit.

Format per entry:

```
## YYYY-MM-DD cycle <n> — task `<task_id>`
**Symptom:** one-line description of what the agent did or what failed.
**Audit:** data/audit/<session_id>.jsonl
**Root cause:** what was actually wrong.
**Fix:** commit <hash> — files / approach. Or "no code fix — LLM reasoning miss" / "no fix — flake, see notes".
**Tests:** added test path / "covered by existing".
```

Keep entries terse. If a cycle finds nothing actionable, do NOT add a noise
entry — only log when there's a real bug, a deliberate decision not to fix,
or a recurring flake worth tracking.

---

## 2026-05-12 cycle 84 — task `screenshot_desktop`
**Symptom:** Step 1 `computer screenshot` failed with `mss is required for
computer screenshots. Install it with: pip install mss` (14ms — never even
attempted a grab). Agent recovered by running `pip install mss` via the
shell tool, then succeeded on step 3.
**Audit:** data/audit/437f9374d70c4b7a87b135307af378c6.jsonl
**Root cause:** `mss` is imported lazily by `ComputerTool._ensure_mss` and
used by every screenshot, vision, color-detection and mouse helper, but it
was never declared in `pyproject.toml` `[project].dependencies`. Fresh
installs hit the friendly error and have to figure it out manually.
**Fix:** added `mss>=10.0.0` to runtime dependencies in `pyproject.toml`.
**Tests:** `tests/unit/test_runtime_deps.py::test_mss_importable` — imports
`mss` and `mss.tools` and asserts the entry points we use exist. Regresses
if the dep is removed from `pyproject.toml`.

---

## 2026-05-12 cycle 81 — task `search_duckduckgo`
**Symptom:** 13-step run with 4 errors. Steps 3, 6 hit `Unknown ref: e172`
on the search combobox (stale-ref recurring flake from cycle 1). Steps
7, 8 sent `browser_interact action=fill target_text="利用 DuckDuckGo 进行搜索"
value="OpenAI"` and got back `"Missing 'value' for fill subaction"` —
despite `value` being clearly populated in `tool_input`. Agent recovered
by navigating to `https://duckduckgo.com/?q=OpenAI` directly. Final
answer was correct (OpenAI / ChatGPT / Wikipedia).
**Audit:** data/audit/2847d7e24deb46918a062a5e9b89d418.jsonl
**Root cause:** Two distinct recurring flakes.
1. Stale ref e172 after the page mutates from a click — same pattern as
   cycle 1, agent assumes refs persist across non-navigations.
2. The `fill` subaction failure is *not* the cycle-65 surrogate-mojibake
   issue (the Chinese here is well-formed UTF-8, no lone surrogates).
   Direct subprocess repro:
   `agent-browser-win32-x64.exe --headed find text "利用 DuckDuckGo 进行搜索" fill OpenAI --json`
   returns `{"success":false,"error":"Element not found..."}` —
   *different* error than the one mimi sees. Suggests context-dependent
   argv handling between mimi's `subprocess.run` path and a direct CLI
   invocation. Root cause not isolated this cycle.
**Fix:** no code fix — recurring flake worth tracking; needs cleaner
repro before changing `interaction.py::fill` or `agent_browser.py`
argv assembly. Agent self-recovered via direct URL.
**Tests:** none (no fix landed).

---

## 2026-05-12 cycle 80 — task `nav_wikipedia`
**Symptom:** 4-step run that should've taken 2. Steps 2-3 both called
`browser_extract action=extract` — step 2 with `instruction="text"`,
step 3 with `instruction="(() => { const paragraphs = ... })()"` (raw
JS code in the instruction field). Both silently returned a generic
`{url, title, text: body.innerText}` page dump via the handler's
catch-all `else` branch. Agent then fell back to `action=eval` at
step 4 and succeeded. Final answer was correct.
**Audit:** data/audit/f20d382390da4e36be522933cc58153b.jsonl
**Root cause:** `actions/extract.py::extract` only matched the seven
documented instructions exactly ("get text", "headings", ...). The
LLM's "text" was a one-word shorthand for "get text", and the JS-as-
instruction was a misunderstanding (the agent thought `extract` would
interpret arbitrary JS like `eval` does). Both fell through to the
undocumented catch-all that returned the whole page — looking close
enough to success that the agent wasted a turn parsing it.
**Fix:** commit 5747c97 — `actions/extract.py::extract` now (a)
resolves the aliases `text` / `get_text` / `gettext` → `get text`,
and (b) returns an `is_error=True` ToolResult listing valid options
when the instruction is unrecognized, nudging the agent toward
`action=eval` for arbitrary JS.
**Tests:** added `test_extract_text_alias_resolves_to_get_text` and
`test_extract_unknown_instruction_returns_error_with_options` in
`tests/unit/test_actions_registry.py`.

---

## 2026-05-12 cycle 71 — task `xft_fresh_login` (recurring)
**Symptom:** 30-step run, 5 errors, ended on CAPTCHA-tail with no final
text — same long-standing pattern as cycles 15/23/31/.../63. New
behaviour worth flagging: step 24 issued
`browser_advanced action=cdp cdp_method=Page.captureScreenshot` in an
attempt to bypass the disabled-screenshot gate, and got
"CDP error: window.__openmimi_cdp_send is not a function". Step 26
`get_box target_text="按住左方滑块..."` failed and the recorded
result_summary shows cp936 mojibake for the Chinese visible text.
Other errors: step 7 wait_for_navigation timeout (recurring), step 10
stale ref e22, step 17 stale ref e52.
**Audit:** data/audit/800e7b1dc08249a69c341771053b1ee8.jsonl
**Root cause:** Two distinct things, both narrow:
- CDP escape hatch depends on a `window.__openmimi_cdp_send` binding
  injected by agent-browser. On this xft page the binding wasn't
  present — likely because the page reload at step 4 happened before
  agent-browser re-exposed the binding, or the site's CSP/Trusted Types
  rejected the injection. The error message itself is correct and
  informative; this is not a tool bug.
- get_box error_summary mojibake is an audit-log decoding glitch:
  agent-browser stderr bytes were UTF-8, but `_exec`'s stderr capture
  on Windows decoded with cp936. Cosmetic — does not affect behaviour.
**Fix:** no code fix — agent should not try CDP screenshot as a gate
bypass (the gate is intentional). The mojibake is worth a follow-up
in `agent_browser.py:_run_subprocess` to force `errors='replace'` +
`encoding='utf-8'` on stderr decode, but deferring until it surfaces
on a non-flake task.
**Tests:** none.

---

## 2026-05-12 cycle 65 — task `search_duckduckgo`
**Symptom:** 13-step run, 4 errors but agent self-recovered and
returned correct top-3 (OpenAI / ChatGPT / Wikipedia). Errors:
- step 3, 7: stale `ref=e172` after page state change (recurring,
  documented cycle 1).
- step 6: `browser_interact action=type target_text="..."` failed
  with "Unknown subaction: type" from agent-browser.
- step 8: `browser_interact action=fill target_text="..." value="OpenAI"`
  failed with "Missing 'value' for fill subaction" — even though
  value was clearly present in the tool input.
**Audit:** data/audit/92a3bd67910948809135aa02b5b23606.jsonl
**Root cause:** the `target_text` payloads in steps 6 and 8 contained
unpaired UTF-16 surrogates (`\udca1`, `\udca2`) picked up from a
mojibake snapshot. On Windows the subprocess CreateProcess argv
encoding chokes on lone surrogates, which corrupts the trailing args.
The downstream agent-browser binary then sees a truncated/garbled
command line — explaining both the "Unknown subaction: type" (the
`type` keyword shifts position) and the "Missing 'value'" rejection
even though we passed value. `find text <target> type <value>` is
actually documented as valid by `agent-browser find --help`.
**Fix:** no code fix this cycle — agent recovered and the bug is
narrow (corrupt-unicode propagating from snapshot). Worth a defensive
sanitizer (`_strip_lone_surrogates`) on ref/target_text/value before
`_exec` in `tools/actions/interaction.py`; deferring until a clearer
repro path. Logged so the pattern is searchable.
**Tests:** none.

---

## 2026-05-12 cycle 64 — task `nav_wikipedia`
**Symptom:** Clean 4-step run extracted the correct first paragraph
("Python is a high-level, general-purpose programming language ...")
at step 4, verifier marked plan complete — but CLI printed
"(no final text)" instead of the paragraph.
**Audit:** data/audit/4d2a45ab3e8d4b799f068eb0566e8a57.jsonl
**Root cause:** `_extract_last_assistant_text` returned on the FIRST
assistant message in reverse order regardless of whether it had text
content. When the loop terminates via the "verifier says done + plan
complete" branch in `loop.py:486-488`, the most recent assistant
message is a `tool_use`-only block (no text) — so the function
returned `""` even though a prior assistant turn had usable text. This
manifested only on plan-completed-via-verifier endings, which is why
xft cycles (max_turns exits) never repro'd it cleanly.
**Fix:** commit 7422423 — `_extract_last_assistant_text` now walks
backwards through all assistant messages and returns the first one with
non-empty text content. Treats empty string content as "skip" too.
**Tests:** added 5 unit tests in `tests/unit/test_orchestrator.py`:
`test_extract_last_assistant_text_returns_text_block`,
`_skips_tool_use_only_assistant`, `_empty_when_no_text_anywhere`,
`_handles_string_content`, `_skips_empty_string_content`.

---

## 2026-05-12 cycle 63 — task `xft_fresh_login` (recurring)
**Symptom:** Steps 1-3 issued `browser_advanced storage clear` /
`clear_cache` before any navigation; all three returned SecurityError
("Failed to read the 'cookie' property" / "'localStorage' property")
because the browser was still on `about:blank`. Agent recovered by
navigating to xft and continuing; session ultimately ended with
"(no final text)" after 31 steps — same CAPTCHA-blocked tail as cycles
15/23/31/39/47/55. Two `get_box e1` stale-ref errors mid-flow (steps
22, 26), self-recovered.
**Audit:** data/audit/a1cc946733dd401cad468122f85dabb4.jsonl
**Root cause:** LLM reasoning miss — task prompt phrased the sequence as
"clear cookies for that domain ... then login fresh", and the agent
read it literally (clear before navigate). Storage APIs on `about:blank`
are blocked by Chromium origin policy, so the error is correct. The
recurring CAPTCHA tail is the same long-standing LLM miss (see cycle 23
notes); not re-investigated this cycle.
**Fix:** no code fix — LLM reasoning miss. The natural workaround is
"navigate to domain → clear storage → reload", which the agent did not
do. Tool surface is correct. Documenting so the pattern is searchable.
**Tests:** none.

---

## 2026-05-12 cycle 25 — task `search_duckduckgo`
**Symptom:** `browser_advanced.wait` calls at steps 2 and 6 sent
`duration_ms: 4000` and `duration_ms: 5000` respectively, but the audit
log shows `"Waited 1000ms"` for both — the audit row truthfully reported
what the handler actually did (default 1000ms wait), making the lie
silent and untraceable.
**Audit:** data/audit/68136973d0d546099621f33550e708ad.jsonl
**Root cause:** `actions/wait.py:wait` only read `inp.get("milliseconds",
1000)`, but `browser_advanced.py:52-54` advertises both `milliseconds`
AND `duration_ms` in the schema. The LLM picked the second documented
name and the handler silently fell back to default. The other wait_*
handlers already accept `timeout_ms` / `milliseconds` aliases, so this
was a localized gap, not a schema-wide problem.
**Fix:** commit 015251a — accept `milliseconds`, `duration_ms`, and
`timeout_ms` in `actions/wait.py:wait` (in that precedence order).
**Tests:** added `test_wait_handler_accepts_duration_ms_alias` and
`test_wait_handler_accepts_timeout_ms_alias` in
`tests/unit/test_actions_registry.py`.

---

## 2026-05-12 cycle 23 — task `xft_fresh_login` (recurring)
**Symptom:** Same as cycle 15 — session hit max_turns at step 30 with
"(no final text)" while attempting to solve the xft slider CAPTCHA.
Login form fill (steps 10-13) worked cleanly thanks to the
react_fill / `_extract_box` fixes from cycles 5 + 15; agent reached the
CAPTCHA then spun on JS-based puzzle-piece analysis (canvas reads,
gradient cols, dark-column maps) for 15 turns without ever issuing a
`mouse_drag`.
**Audit:** data/audit/87124fc20bc247f595cb613e7203d03b.jsonl
**Root cause (compound):**
1. LLM-reasoning miss — agent over-investigates the puzzle image and
   never commits to the actual drag motion. Same as cycle 15.
2. The task prompt explicitly says "solve it via the OS-level computer
   tool (focus_window → screenshot → detect_color → mouse_drag)" but
   `computer.screenshot` is gated by `OPENMIMI_ENABLE_SCREENSHOTS`,
   which is OFF in this environment. Step 17 returned the "Screenshots
   disabled" guidance. The task as written is therefore unsolvable in
   the current cron env regardless of LLM quality.
**Fix:** none — would need either the env flag enabled in cron, or a
fundamentally better slider-CAPTCHA solver. Two consecutive cycles
(15, 23) confirm this as a load-bearing recurring failure for task[7]
and a chain failure source for task[6] (`xft_after_login`).
**Tests:** n/a.
**Notes:** Step 23 errored with `TypeError: canvases.map is...` — that
was from the agent's own JS, not openmimi code. Tab accumulation
continues but didn't bite this cycle.

## 2026-05-12 cycle 22 — task `xft_after_login`
**Symptom:** Task expected the persistent profile to already be logged
in; agent navigated to xft.cmbchina.com and found the public login form
instead. Agent correctly diagnosed "profile did NOT retain an active
login session" and returned an honest "cannot proceed" report after 13
steps / 29 LLM turns.
**Audit:** data/audit/45db2e288b33423faad7ef9097afc18b.jsonl
**Root cause:** Chain dependency on task[7] `xft_fresh_login` (cycle 15)
succeeding: cycle 15 cleared cookies then failed to solve the slider
CAPTCHA, leaving the profile permanently logged-out. Every subsequent
`xft_after_login` will fall through to "no session" until something
re-establishes the login. No code bug — purely an environmental drift
problem on the test fixture.
**Fix:** none — would require the LLM to actually solve the slider
CAPTCHA in cycle 15 (LLM-reasoning), or a session-file restore step
prepended to the task prompt (would change task identity, README
forbids without good reason).
**Tests:** n/a.
**Notes:** Step 4 `memory_read sites/xft.cmbchina.com.md` returned
TARGET_NOT_FOUND (file is `.json` not `.md`); agent self-recovered at
step 7. Minor LLM file-extension miss, not worth a fix. The 12 stale
tabs from prior cycles still recur as background noise.

## 2026-05-12 cycle 19 — task `tabs_example`
**Symptom:** Task succeeded but burned 13 tool steps / 29 LLM turns for
what should be 4 calls. Agent opened the 3 tabs, then
`tab_switch tab_index=2` landed on a stale xft.cmbchina.com tab (title:
`招商银行薪福通...`). It had to call `tab_list`, see 14 total tabs
(2 stale xft, then example.com / 2 blanks / example.org / example.net,
plus more), `tab_switch` to tab 4 (still wrong), then `tab_switch` to
tab 14, and finally `get_title` → "Example Domain".
**Audit:** data/audit/d2f179723c714772a87c1c71ae39c8b8.jsonl
**Root cause:** Same persistent-profile session-restore behaviour
called out in cycle 3 (`xft_browser_profile/` keeps tabs alive across
mimi runs). cycle 3 said "Could revisit with a tab_new-without-leading-
blank policy if it recurs." It recurred, and got worse. The deeper
problem: `tab_new` reported only `"New tab opened: <url>"` — the LLM
had no way to know what absolute index its new tab landed on without a
follow-up `tab_list`.
**Fix:** commit 5eb7b3c — `tab_new` now reports
`"New tab opened: <url> (now at tab N of M)"` and exposes
`new_tab_index` in `details`. LLM can `tab_switch tab_index=N` without
guessing.
**Tests:** `tests/unit/test_actions_registry.py::test_tab_new_reports_new_tab_index`
— simulates a profile with 13 stale tabs already open, asserts the
absolute index (14) appears in both `output` and `details`.
**Notes:** Does NOT address the underlying stale-tab accumulation
(Chrome session-restore reopening earlier-cycle tabs). A `--restore-last-session=false`
launch flag for the persistent profile would be the proper fix; that
lives below the agent-browser layer.

## 2026-05-12 cycle 15 — task `xft_fresh_login`
**Symptom:** Step 14 `react_fill ref=e22 value=18584828398` →
`react_fill failed: could not resolve ref e22`, even though e22 was a
live ref in the immediately preceding snapshot. Same root cause silently
breaking 7 other handlers (force-click / right_click / double_click /
get_box / wait_for / wait_for_disappear / scroll_until — `wait_for_disappear`
was the worst, always returning "disappeared" immediately).
**Audit:** data/audit/a269b309af10451c9c7351985fccc35e.jsonl
**Root cause:** `agent-browser get box <ref> --json` emits
`{"success":true,"data":{"x":..,"y":..,"width":..,"height":..},"error":null}`
— box fields are FLAT on `data`. All eight production callers were doing
`data.get("box")` and silently getting `None`. Cycle 7's react_fill fix
appeared to work only because its unit test mocked the wrong (wrapped)
shape. Verified against the live binary: `agent-browser get box e1 --json`
returns the flat envelope.
**Fix:** commit dca6c59 — added module-level `_extract_box` helper in
`src/openmimi/tools/agent_browser.py` tolerating both shapes; wired all
8 call sites (`actions/interaction.py`, `actions/extract.py`,
`actions/scroll.py`, `actions/wait.py`) through it.
**Tests:** `tests/unit/test_actions_registry.py` —
`test_react_fill_with_ref_handles_flat_box_response` (real envelope shape),
`test_extract_box_helper_accepts_both_shapes`.
**Notes:** LLM-side, the session also hit a slider CAPTCHA
(`按住左方滑块，向右拖动滑块完成拼图`) and didn't solve it, and burned
several turns acting on stale refs after reload (steps 9/16/17/21).
Logged as LLM-reasoning misses; no code change.

## 2026-05-12 cycle 12 — task `screenshot_desktop`
**Symptom:** Step 4 `computer batch [{action:shell,command:"setx OPENMIMI_ENABLE_SCREENSHOTS 1"},{action:screenshot}]`
returned `Step 1 (shell): EXCEPTION - name 'subprocess' is not defined`.
Worse, the batch as a whole reported `is_error=false` despite the shell
sub-step crashing, so `mimi audit-stats` would see a clean row.
**Audit:** data/audit/3603bd84e4604ff2b97ee47dd54b4b6f.jsonl
**Root cause:** Two bugs in `src/openmimi/tools/computer.py`:
1. `ComputerTool._do_shell` references `subprocess.run` / `subprocess.
   TimeoutExpired` at lines 1758 + 1781, but `import subprocess` was
   function-local inside `_do_launch` (line 1315) — never at module scope.
   Calling the action from anywhere outside that function raised
   `NameError: name 'subprocess' is not defined`.
2. `_do_batch` set `is_error=has_error if bail else False`. Bail=false
   means "keep going", not "treat errors as success" — callers and
   `mimi audit-stats` still need the failure signal, otherwise the only
   place it surfaces is in the human-readable summary string.
**Fix:** commit 233529d — `import subprocess` moved to module scope
(line 22 of computer.py); `_do_batch` now returns `is_error=has_error`
unconditionally.
**Tests:** `tests/unit/test_computer_shell_action.py::test_do_shell_runs_without_nameerror`
+ `test_do_batch_with_shell_substep_does_not_raise_subprocess_nameerror`
+ `test_do_batch_marks_is_error_when_substep_fails_even_with_bail_false`.
The first two monkeypatch `subprocess.run` and assert the handler reaches
it without NameError; the third stubs `_dispatch` to fail and asserts
`is_error=True` even with `bail=false`.
**Side observations (no code fix):**
- Step 1 `computer screenshot` returned the screenshot-gate message
  ("Screenshots disabled by default. Set OPENMIMI_ENABLE_SCREENSHOTS=1
  or pass --screenshots to enable."). Working as designed — gate is a
  privacy/token-cost guard from commit 11aef8d. The task as written is
  unfulfillable unless the user opts in; agent adapted via `list_windows`
  and produced a reasonable textual answer.
- Step 2 `shell echo $OPENMIMI_ENABLE_SCREENSHOTS` ran on Windows cmd.exe
  and printed the literal string `$OPENMIMI_ENABLE_SCREENSHOTS`. Same
  Linux-syntax-on-Windows LLM-reasoning pattern documented in cycle 6.
- The agent's choice of `setx ...` to enable screenshots wouldn't have
  worked even if subprocess had imported: `setx` writes a permanent user
  env var, it does NOT affect the current Python process's environment.
  Pure LLM-reasoning miss.
- Step 6 `tab_list` showed 10 tabs including stale xft/example/duckduckgo
  tabs from previous goldset cycles, persisted via the now-enabled
  `xft_browser_profile/`. Same Chrome session-restore behaviour noted in
  cycle 7's side observations.


**Symptom:** Step 11 `browser_interact {"action":"react_fill","ref":"e22","value":"18584828398"}`
failed with `react_fill failed: element not found`, despite the snapshot from
step 9 listing `textbox "请输入手机号" [ref=e22]`. Agent recovered by hand-rolling
JS eval-based fills (steps 15/16) and never used react_fill again.
**Audit:** data/audit/eab0029689574fad92838493fe29579e.jsonl
**Root cause:** `actions/interaction.py::react_fill` did
`css_selector = ref.lstrip("@")` and embedded that into
`document.querySelector(css_selector)`. Agent-browser snapshot refs (e.g.
`"e22"`) are NOT CSS selectors — they're opaque handles into the latest
snapshot, resolved server-side by the agent-browser CLI. `querySelector("e22")`
matches no element in the DOM, so the call always returned `{error: 'element
not found'}`. All sibling handlers (`click`, `fill`, `type`, `check`) correctly
delegate refs via `engine._exec("click", ref, …)`; only `react_fill` had this
querySelector shortcut.
**Fix:** commit 254265f — `react_fill` now resolves a ref via
`engine._exec("get", "box", ref, …)`, then locates the element with
`document.elementFromPoint(centerX, centerY)` (walking up to the nearest
input/textarea/select if needed) before running the React-aware prototype
setter. `target_text` path is unchanged.
**Tests:** `tests/unit/test_actions_registry.py::test_react_fill_with_ref_resolves_box_then_uses_elementFromPoint`
+ `test_react_fill_with_ref_reports_error_when_box_unresolvable` — assert
the first `_exec` is `("get", "box", "e22", "--json")`, the eval JS uses
`document.elementFromPoint` and does NOT embed `querySelector("e22")`, and
unresolvable refs surface a clear error.
**Side observations (no code fix):**
- Step 14 `type ref=e22` → "Unknown ref: e22": LLM tried to type without
  re-snapshotting after the click in step 13. agent-browser refs invalidate
  per snapshot generation — documented behavior (see cycle 1 side observation).
- Step 17 `click ref=e21` → "Unknown ref: e21": same staleness pattern; the
  agent had run several non-snapshot ops since the last snapshot.
- Step 23 `eval` → `ReferenceError: clearAssit is not defined`: the site's own
  `onclick` handler at xft.cmbchina.com:1694 references a missing function.
  Site-side JS bug, surfaces as `is_error=true` only because it bubbled out of
  agent-browser's eval — not actionable on our side.
- Step 29 `tab_list` showed 8 tabs; tabs 3-5 were `example.com/.org/.net` from
  cycle 3's `tabs_example` task. The xft profile (`xft_browser_profile/`) is
  configured in `~/.openmimi/config.json` (or via browser.user_data_dir), and
  Chrome's session-restore is re-opening tabs left by earlier cycles. Possible
  future cleanup: agent-browser could explicitly close non-task tabs on start,
  or `tabs_example` could close its tabs at end. Logged but not fixed.
- Run hit `max_turns=30` (configured globally in `~/.openmimi/config.json`),
  not the schema default of 50. Working as configured — explicit user choice,
  not a bug.

---

## 2026-05-12 cycle 6 — task `xft_after_login`
**Symptom:** Task asked the agent to use the already-logged-in xft.cmbchina.com
session via the persistent profile, but `mimi run` landed on the public marketing
page (with "登录"/"免费注册" visible). Agent then burned 30 tool steps / 62 LLM
turns trying alternate logins, shell file searches, etc., and hit `max_turns`
with "(no final text)".
**Audit:** data/audit/ac204aca6592486cb86ae73ccf4eea79.jsonl
**Root cause:** `xft_browser_profile/` exists at repo root with valid Chrome
profile data (Default/, Local State, first_party_sets.db, ...), and
`AgentBrowserTool.__init__` accepts `user_data_dir`, but the orchestrator never
passed it and `BrowserConfig` had no field for it. So every `mimi run` got a
fresh ephemeral profile — the saved cookies/login were unreachable.
**Fix:** commit b3a2304 — added `user_data_dir: Path | None = None` to
`BrowserConfig` (`config/schema.py`), and `orchestrator.py:217-226` now passes
`user_data_dir=str(cfg.browser.user_data_dir) if cfg.browser.user_data_dir else None`
to `AgentBrowserTool(...)`. To actually use the xft profile, set
`{"browser":{"user_data_dir":"xft_browser_profile"}}` in `.openmimi.json`.
**Tests:** `tests/unit/test_orchestrator.py::test_from_env_passes_user_data_dir_when_configured`
+ `test_from_env_user_data_dir_defaults_to_none` — assert the kwarg flows through
when set and stays `None` by default.
**Side observations (no code fix):**
- Step 24 `shell dir /s /b C:\*xft*.jsonl ...` searched the entire C: drive and
  was killed by the 300s shell timeout (`TOOL_INTERNAL_ERROR`). Tool behaved
  correctly — bounded a doomed full-disk scan. LLM-reasoning miss: should have
  scoped the search to the project directory or used Glob-style narrow patterns.
- Steps 10/15/22 ran Linux-style paths (`/root/.config/...`, `find /`) on Windows.
  cmd.exe returned cp936-encoded mojibake stderr but the tool flagged
  `is_error=true` correctly. LLM-reasoning miss — agent should have read the
  earlier "系统找不到指定的路径" feedback rather than retrying variants.
- The 30-step ceiling came from `loop.py::sampling_loop`'s function default
  `max_turns: int = 30`, even though `AppConfig.max_turns` defaults to 50. The
  orchestrator does pass `cfg.max_turns` explicitly, so this didn't bite here —
  but the mismatched defaults are a future trap. Filed as a follow-up note;
  not patched this cycle.

---

## 2026-05-12 cycle 3 — task `tabs_example`
**Symptom:** Task completed but `mimi replay 4e1f0585876d4bd2b40658ade4bad298`
crashed with `IndexError: list index out of range` partway through.
**Audit:** data/audit/4e1f0585876d4bd2b40658ade4bad298.jsonl
**Root cause:** `cli.py::replay` did
`(rec.get("result_summary") or "").splitlines()[0]`. Step 8 was
`browser_extract get_title` on an empty active tab, which returned
`result_summary=""`. `"".splitlines()` is `[]`, so `[0]` blew up and
the whole replay aborted halfway. Replay should be defensive about
audit-log content — one weird record shouldn't blow away the rest of
the session view.
**Fix:** commit 8a6d38e — fall back to `""` when `splitlines()` is
empty (`cli.py:276-279`).
**Tests:** `tests/unit/test_cli.py::test_replay_handles_empty_result_summary`
— audit file with one `result_summary=""` record; asserts exit_code==0
and the row still renders.
**Side observation (no fix):** `tab_switch tab_index=2` landed the agent
on an about:blank pre-existing tab (URL=about:blank, title=""), so it
re-navigated tab 2 to example.org at step 12 to recover. tab_new
appears to leave an initial blank tab in position 1 that the LLM didn't
account for; the agent assumed [1=com, 2=org, 3=net] but the actual
order was [1=blank, 2=com, 3=org, 4=net]. This is an LLM-reasoning
miss (should call `tab_list` first to see real indices), not a tool
bug. Could revisit with a tab_new-without-leading-blank policy if it
recurs.

---

## 2026-05-12 cycle 1 — task `search_duckduckgo`
**Symptom:** step 7 `browser_navigate {"action":"wait_for_navigation","milliseconds":15000}`
timed out after **10000ms** despite the LLM asking for 15000ms.
**Audit:** data/audit/545114a81ca04df099d8aa0b42c06dcb.jsonl
**Root cause:** `actions/wait.py::wait_for_navigation` only read
`inp.get("timeout_ms", 10000)` — the `milliseconds` key was silently
dropped and the default 10s was used. Schema for `timeout_ms` only
mentioned `wait_for` and `wait_for_network_idle`, omitting
`wait_for_navigation`, so the LLM had no clear name to use and picked
`milliseconds` (the natural shorthand). The audit log was indistinguishable
from a genuine "page actually didn't navigate" timeout, which is the worst
kind of silent failure.
**Fix:** commit 4cf7bf5 — handler now accepts `timeout_ms` OR `milliseconds`
(`timeout_ms` wins if both present); schema description for `timeout_ms`
now lists wait_for_navigation explicitly and notes the `milliseconds`
fallback so the LLM picks the canonical name next time.
**Tests:** `tests/unit/test_actions_registry.py::test_wait_for_navigation_accepts_milliseconds_alias`
— stuck-URL fake engine, asserts handler bails in <1.5s with `milliseconds=200`
(would take 10s if alias were still ignored).
**Side observation (no fix):** step 3 `fill` on `ref: e172` failed with
"Unknown ref" because the agent had just called `click` on the same ref
without re-snapshotting. agent-browser refs are tied to snapshot generations;
this is an LLM-reasoning miss, not a tool bug. Agent recovered via a fresh
snapshot at step 4 and completed the task.

---

## 2026-05-11 cycle 0 — task `nav_wikipedia`
**Symptom:** `mimi run "<task>"` printed the chat welcome banner, hit stdin
EOF, exited 0, and produced no audit log. Cron captured the welcome screen
in `data/goldset_runs/cycle_000_nav_wikipedia.log`; no
`data/audit/49e567f0e2ad4fc2be46b3a81eb25909.jsonl` was ever written.
**Audit:** N/A — no audit was produced (that *was* the bug).
**Root cause:** `pyproject.toml` binds `mimi` to `openmimi.cli:chat_main`,
which dropped straight into the chat REPL ignoring `sys.argv`. So
`mimi run "..."`, `mimi audit-stats`, `mimi replay <sid>` all silently
fell through to the REPL.
**Fix:** commit e209a7f — `chat_main` now sniffs `sys.argv[1]` and
hands off to `app()` whenever it matches a registered typer subcommand
(derived from `app.registered_commands`) or is `--help` / `-h`. Bare
`mimi` with no args still enters the REPL.
**Tests:** `tests/unit/test_cli.py::test_chat_main_routes_subcommands_to_typer_app`,
`test_chat_main_routes_help_flag_to_typer_app`,
`test_chat_main_bare_invocation_skips_typer_app`.
**Re-run:** task ran cleanly post-fix as session
`0874be7dd8e6438b941e9b5bdc465d25`; both tool calls (`browser_navigate`,
`browser_extract`) succeeded and the agent returned the Wikipedia opener
paragraph.
