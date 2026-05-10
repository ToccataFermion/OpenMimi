"""Lightweight session replay renderer (roadmap #11).

Stage 1 shipped ``build_html(records, *, title=None) -> str``: a pure
function that turns a list of episodic step records into a single
self-contained HTML page using only stdlib (``html.escape`` + an
inline ``<style>`` block). No external CSS, no JavaScript framework,
no third-party deps.

Stage 2 adds the ``python -m openmimi.replay <session_id>`` CLI plus
helpers to load + merge episodic JSONL (``data/memory/episodic``) with
audit JSONL (``data/audit``) so the same page can surface tool inputs,
durations, and screenshots. ``image_path`` fields are rendered as
``<img>`` tags with the path preserved as-is (relative or absolute) so
the file: URL works when the HTML lives next to ``data/``.
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

_log = logging.getLogger(__name__)

_DEFAULT_EPISODIC_DIR = Path("data/memory/episodic")
_DEFAULT_AUDIT_DIR = Path("data/audit")

# Image extensions we render inline with <img>. Anything else (paths
# pointing at PDFs, archives, etc.) falls back to the JSON-payload
# rendering so the user still sees the path string but the browser
# doesn't try to embed an unsupported asset.
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

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
summary .dur   {{ color: #6a737d; margin-left: 0.5rem; font-size: 0.8rem; }}
pre  {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        font-size: 0.8rem; background: #f6f8fa; padding: 0.5rem;
        border-radius: 4px; white-space: pre-wrap; word-break: break-word;
        margin: 0.5rem 0 0 0; }}
img.screenshot {{ max-width: 100%; border: 1px solid #d0d7de;
                  border-radius: 4px; margin-top: 0.5rem; display: block; }}
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
    ``url`` / ``domain``). Audit-only fields (``tool_input`` /
    ``image_path`` / ``duration_ms``) are also recognised: ``image_path``
    renders as an inline ``<img>`` and ``duration_ms`` appears in the
    summary. ``title`` defaults to the first record's ``session_id``,
    falling back to ``"OpenMimi replay"``.

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
    image_path = record.get("image_path") or ""
    duration_ms = record.get("duration_ms")

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
    duration_html = (
        f'<span class="dur">{html.escape(str(duration_ms))}ms</span>'
        if isinstance(duration_ms, (int, float))
        else ""
    )

    summary_line = (
        f'<summary>'
        f'<span class="step">{html.escape(step_text)}</span>'
        f'<span class="tool">{html.escape(str(tool))}</span>'
        f'{action_html}'
        f'{status_html}'
        f'{url_html}'
        f'{duration_html}'
        f'</summary>'
    )

    body_parts: list[str] = []
    if summary:
        body_parts.append(f'<pre>{html.escape(str(summary))}</pre>')

    # Render any other interesting keys (ts, domain, sub_session_id,
    # tool_input, etc.) as a compact JSON block. image_path is pulled
    # out into its own <img> tag and excluded from the JSON payload to
    # keep the dump readable.
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
            "image_path",
            "duration_ms",
        }
    }
    if interesting:
        body_parts.append(
            "<pre>"
            + html.escape(
                json.dumps(interesting, ensure_ascii=False, indent=2, default=str)
            )
            + "</pre>"
        )

    if image_path and _looks_like_image(str(image_path)):
        body_parts.append(
            f'<img class="screenshot" src="{html.escape(str(image_path))}" '
            f'alt="step {html.escape(step_text)} screenshot">'
        )

    return f"<details{css_class}>{summary_line}{''.join(body_parts)}</details>"


def _looks_like_image(path: str) -> bool:
    """True when *path* has a familiar raster/vector extension.

    We don't open the file — replay should work on session bundles
    where the screenshots have been moved or pruned. The extension
    test alone is enough to decide whether ``<img>`` is the right
    tag to emit.
    """
    suffix = Path(path).suffix.lower()
    return suffix in _IMG_EXTS


# ---------------------------------------------------------------------------
# JSONL loaders + merge
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read all dict rows from *path*; skip blanks and malformed lines."""
    out: list[dict[str, Any]] = []
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError as exc:
        _log.warning("cannot open %s: %s", path, exc)
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                _log.warning("skipping malformed JSON in %s", path)
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def load_audit_session(
    session_id: str, audit_dir: Path | str = _DEFAULT_AUDIT_DIR
) -> list[dict[str, Any]]:
    """Read ``<audit_dir>/<session_id>.jsonl`` and return its records.

    Returns an empty list if the file doesn't exist — replay should
    still work when only one of {episodic, audit} is present.
    """
    path = Path(audit_dir) / f"{session_id}.jsonl"
    if not path.is_file():
        return []
    return _read_jsonl(path)


def load_episodic_session(
    session_id: str,
    episodic_dir: Path | str = _DEFAULT_EPISODIC_DIR,
) -> list[dict[str, Any]]:
    """Read all episodic step rows for *session_id*.

    Walks every ``YYYY-MM/`` subdirectory under *episodic_dir* (the
    same layout ``EpisodicStore`` writes) and stitches them back into
    chronological order by ``(step, ts)``.
    """
    base = Path(episodic_dir)
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for month in sorted(p for p in base.iterdir() if p.is_dir()):
        f = month / f"{session_id}.jsonl"
        if f.is_file():
            out.extend(_read_jsonl(f))
    out.sort(key=lambda r: (r.get("step", 0), r.get("ts", "")))
    return out


def merge_records(
    episodic: Iterable[Mapping[str, Any]],
    audit: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Union episodic + audit step rows, keyed by ``step``.

    When both sides describe the same step we take episodic as the
    base (it's the richer record) and patch in audit-only fields
    (``tool_input`` / ``image_path`` / ``duration_ms``). Steps present
    on only one side pass through unchanged. Output is sorted by step.

    Non-dict rows and rows without a numeric ``step`` are dropped —
    the merge needs a stable key to align.
    """
    merged: dict[int, dict[str, Any]] = {}

    def _key(r: Mapping[str, Any]) -> int | None:
        s = r.get("step")
        return s if isinstance(s, int) and not isinstance(s, bool) else None

    for r in episodic:
        if not isinstance(r, Mapping):
            continue
        k = _key(r)
        if k is None:
            continue
        merged[k] = dict(r)

    for r in audit:
        if not isinstance(r, Mapping):
            continue
        k = _key(r)
        if k is None:
            continue
        if k in merged:
            target = merged[k]
            for field in ("tool_input", "image_path", "duration_ms"):
                if field in r and field not in target:
                    target[field] = r[field]
            # Backfill error_code / is_error if episodic forgot.
            for field in ("error_code",):
                if field in r and not target.get(field):
                    target[field] = r[field]
        else:
            merged[k] = dict(r)

    return [merged[k] for k in sorted(merged)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m openmimi.replay",
        description=(
            "Render a saved OpenMimi session as a single-page HTML "
            "replay (episodic + audit logs merged)."
        ),
    )
    parser.add_argument("session_id", help="Session id to replay")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output HTML path (default: replay_<sid>.html in CWD)",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=_DEFAULT_AUDIT_DIR,
        help=f"Audit JSONL directory (default: {_DEFAULT_AUDIT_DIR})",
    )
    parser.add_argument(
        "--episodic-dir",
        type=Path,
        default=_DEFAULT_EPISODIC_DIR,
        help=f"Episodic JSONL root (default: {_DEFAULT_EPISODIC_DIR})",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="HTML <title>; defaults to the session id",
    )
    return parser


def render_session(
    session_id: str,
    *,
    audit_dir: Path | str = _DEFAULT_AUDIT_DIR,
    episodic_dir: Path | str = _DEFAULT_EPISODIC_DIR,
    title: str | None = None,
) -> str:
    """High-level helper: load both sources, merge, return HTML.

    Convenience wrapper around ``load_*`` + ``merge_records`` +
    ``build_html`` for callers that don't want to wire each step
    themselves. Used by the CLI and re-exported for embedding.
    """
    episodic = load_episodic_session(session_id, episodic_dir)
    audit = load_audit_session(session_id, audit_dir)
    merged = merge_records(episodic, audit)
    return build_html(merged, title=title or session_id)


def _cli(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    html_text = render_session(
        args.session_id,
        audit_dir=args.audit_dir,
        episodic_dir=args.episodic_dir,
        title=args.title,
    )
    out_path = args.out or Path(f"replay_{args.session_id}.html")
    out_path.write_text(html_text, encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())


__all__ = [
    "build_html",
    "load_audit_session",
    "load_episodic_session",
    "merge_records",
    "render_session",
]
