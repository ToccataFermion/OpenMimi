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
**Fix:** commit <pending> — added `user_data_dir: Path | None = None` to
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
