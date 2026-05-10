"""Lightweight session replay renderer (roadmap #11).

Stage 1 ships ``build_html(records, *, title=None) -> str``: a pure
function that turns a list of episodic step records (the same shape
``EpisodicStore.read_session`` returns) into a single self-contained
HTML page using only stdlib (``html.escape`` + an inline ``<style>``
block). No external CSS, no JavaScript framework, no third-party deps.

Stage 2 (a future wakeup) will add the ``python -m openmimi.replay
<session_id>`` CLI and merge audit logs to surface tool inputs and
screenshots. Keeping the renderer a pure function now means the CLI
will be a thin shell over it, and tests can assert structure without
touching the filesystem.
"""
from __future__ import annotations

import html
import json
from typing import Any, Iterable, Mapping

_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        margin: 1.5rem; max-width: 1000px; color: #1b1f23; }}
h1   {{ font-size: 1.4rem; margin-bottom: 0.25rem; }}
.meta {{ color: #6a737d; font-size: 0.85rem; margin-bottom: 1.5rem; }}
details {{ border: 1px solid #d0d7de; border-radius: 6px;
           margin-bottom: 0.5rem; padding: 0.5rem 0.75rem;
           background: #ffffff; }}
details[open] {{ background: #f6f8fa; }}
details.is-error {{ border-color: #cf222e; background: #ffebe9; }}
summary {{ cursor: pointer; font-family: ui-monospace, SFMono-Regular,
                  Consolas, monospace; font-size: 0.9rem; }}
summary .step  {{ color: #6a737d; margin-right: 0.5rem; }}
summary .tool  {{ font-weight: 600; }}
summary .action {{ color: #0969da; margin-left: 0.25rem; }}
summary .ok    {{ color: #1a7f37; margin-left: 0.5rem; }}
summary .err   {{ color: #cf222e; margin-left: 0.5rem; font-weight: 600; }}
summary .url   {{ color: #6a737d; margin-left: 0.5rem; font-size: 0.8rem; }}
pre  {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        font-size: 0.8rem; background: #f6f8fa; padding: 0.5rem;
        border-radius: 4px; white-space: pre-wrap; word-break: break-word;
        margin: 0.5rem 0 0 0; }}
.empty {{ color: #6a737d; font-style: italic; }}
</style>
</head>
<body>
<h1>{header}</h1>
<div class="meta">{meta}</div>
{body}
</body>
</html>
"""

_EMPTY_BODY = '<p class="empty">No step records in this session.</p>'


def build_html(
    records: Iterable[Mapping[str, Any]],
    *,
    title: str | None = None,
) -> str:
    """Render *records* as a self-contained HTML page.

    *records* is an iterable of dict-like step records following the
    episodic-store schema (``ts`` / ``session_id`` / ``step`` / ``tool``
    / ``action`` / ``result_summary`` / ``is_error`` / ``error_code`` /
    ``url`` / ``domain``). Unknown keys are tolerated; missing keys
    render as blanks. ``title`` defaults to the first record's
    ``session_id``, falling back to ``"OpenMimi replay"``.

    The return value is a complete HTML5 document — pipe it to a file
    and open in a browser.
    """
    record_list = [r for r in records if isinstance(r, Mapping)]
    sid = _first_session_id(record_list)
    page_title = title or sid or "OpenMimi replay"
    header = f"Session {html.escape(sid)}" if sid else "OpenMimi replay"
    meta = _build_meta(record_list)
    body = _build_body(record_list)
    return _HTML_TEMPLATE.format(
        title=html.escape(page_title),
        header=header,
        meta=meta,
        body=body,
    )


def _first_session_id(records: list[Mapping[str, Any]]) -> str:
    for r in records:
        sid = r.get("session_id")
        if isinstance(sid, str) and sid:
            return sid
    return ""


def _build_meta(records: list[Mapping[str, Any]]) -> str:
    if not records:
        return "0 steps"
    count = len(records)
    errors = sum(1 for r in records if r.get("is_error"))
    first_ts = next(
        (
            r.get("ts")
            for r in records
            if isinstance(r.get("ts"), str)
        ),
        None,
    )
    last_ts = next(
        (
            r.get("ts")
            for r in reversed(records)
            if isinstance(r.get("ts"), str)
        ),
        None,
    )
    parts = [f"{count} step{'s' if count != 1 else ''}"]
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    if first_ts and last_ts and first_ts != last_ts:
        parts.append(f"{html.escape(first_ts)} → {html.escape(last_ts)}")
    elif first_ts:
        parts.append(html.escape(first_ts))
    return " · ".join(parts)


def _build_body(records: list[Mapping[str, Any]]) -> str:
    if not records:
        return _EMPTY_BODY
    return "\n".join(_render_step(r) for r in records)


def _render_step(record: Mapping[str, Any]) -> str:
    step = record.get("step")
    tool = record.get("tool") or "<unknown>"
    action = record.get("action") or ""
    is_error = bool(record.get("is_error"))
    error_code = record.get("error_code") if is_error else None
    url = record.get("url") or ""
    summary = record.get("result_summary") or ""

    css_class = " class=\"is-error\"" if is_error else ""
    step_text = f"#{step}" if step is not None else "#?"
    action_html = (
        f'<span class="action">{html.escape(str(action))}</span>'
        if action
        else ""
    )
    status_html = (
        f'<span class="err">ERROR {html.escape(str(error_code or ""))}</span>'
        if is_error
        else '<span class="ok">ok</span>'
    )
    url_html = (
        f'<span class="url">{html.escape(str(url))}</span>' if url else ""
    )

    summary_line = (
        f'<summary>'
        f'<span class="step">{html.escape(step_text)}</span>'
        f'<span class="tool">{html.escape(str(tool))}</span>'
        f'{action_html}'
        f'{status_html}'
        f'{url_html}'
        f'</summary>'
    )

    body_parts: list[str] = []
    if summary:
        body_parts.append(
            f'<pre>{html.escape(str(summary))}</pre>'
        )
    # Render any other interesting keys (ts, domain, sub_session_id, etc.)
    # as a compact JSON block so the user can see raw payload too.
    interesting = {
        k: v
        for k, v in record.items()
        if k
        not in {
            "step",
            "tool",
            "action",
            "is_error",
            "error_code",
            "url",
            "result_summary",
            "session_id",
        }
    }
    if interesting:
        body_parts.append(
            "<pre>"
            + html.escape(json.dumps(interesting, ensure_ascii=False, indent=2, default=str))
            + "</pre>"
        )

    return f"<details{css_class}>{summary_line}{''.join(body_parts)}</details>"


__all__ = ["build_html"]
