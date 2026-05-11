# Goldset — autonomous regression tests for mimi

This directory holds a small, fixed set of end-to-end tasks that exercise the
browser tool, the computer tool, and combinations of both. A scheduled cron
job fires every 6 minutes; on each fire Claude picks the next task in
rotation, runs it via `mimi run`, observes the audit log, and fixes any
tool-level bugs it finds.

The goldset is a **regression catch** — not a benchmark. The tasks are
deliberately chosen so that a working mimi can complete them; persistent
failure on the same task across cycles means something regressed in the code,
not that the task is unsolvable.

## Files

- `tasks.json` — the fixed task list. Each task has `id`, `tags`, and a
  `prompt` passed verbatim to `mimi run`. Do not edit task prompts in-place
  without a good reason — the rotation depends on stable identity.
- `state.json` — rotation state. `next_index` is the position into
  `tasks.tasks[]` to run on the next cycle; it wraps at the array length.
  `last_session` is the session id of the most recent `mimi run`, useful for
  pulling the right audit log.
- `issues.md` — reverse-chronological log of every actionable finding from
  the autonomous cycles. Read this to see what regressed and how it was
  fixed, without diffing the codebase.

## Cycle protocol

When the cron fires:

1. Read `state.json` to find `next_index`. The task to run is
   `tasks[next_index % len(tasks)]`.
2. Run the task: `mimi run "<prompt>"`. Capture the session id from stdout.
3. Inspect the audit log at `data/audit/<session_id>.jsonl`. Look for:
   - tool calls with `is_error: true`
   - tools that returned but didn't accomplish what the prompt asked
   - reasoning that loops on the same failure
4. If the failure is a clear tool-level bug (dispatch error, schema mismatch,
   subprocess quoting, foreground-lock flake, etc.) → fix the code, add a
   unit test, run the full suite, commit to `main`.
5. If the failure is an LLM reasoning miss (picked wrong selector, gave up
   too early, etc.) → note it in the turn summary, do not change code.
6. **Log every actionable finding to `issues.md`** — one entry per real bug,
   per deliberate no-fix decision, or per recurring flake. Format is in the
   file's header. Skip the log if the cycle was clean — don't add noise.
7. Bump `next_index`, update `last_session`/`last_cycle_ts`/`cycles_completed`
   in `state.json`.

## xft credentials

The xft tasks use real credentials baked into the prompt (account
`18584828398`, password `Liszt123`). These are recorded verbatim in the
audit log of any cycle that runs an xft task. The user explicitly authorised
this for goldset use; do not redact or hash them — that would diverge the
goldset from what mimi sees in production.

## Profile rotation

Two of the eight tasks involve xft.cmbchina.com:

- `xft_after_login` — relies on the persistent profile in
  `xft_browser_profile/` already holding a logged-in session.
- `xft_fresh_login` — clears cookies first to exercise the full login +
  slider-CAPTCHA workflow.

They alternate naturally as the 8-task rotation cycles through.

## Why every 6 min, why this size

The user set the cadence. Eight tasks × 6 min = ~48 min to traverse the full
set once. That gives a regression signal within an hour of any new commit
landing in `main`, without saturating LLM spend.
