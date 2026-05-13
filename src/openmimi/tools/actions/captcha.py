"""CAPTCHA-specific actions for AgentBrowserTool.

- slider_find_gap: OpenCV-based puzzle-gap detection.
- slider_drag_cdp: CDP ``Input.dispatchMouseEvent`` drag for slider CAPTCHAs.
"""
from __future__ import annotations

import asyncio
import io
import json
import base64
import mimetypes
import os
import re
import tempfile
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

import numpy as np

from ...utils.trajectory import generate_trajectory
from ..result import ToolResult
from . import register

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool


def _download_image(url: str) -> str:
    """Download *url* to a temporary file and return the local path."""
    parsed = urllib.parse.urlparse(url)
    suffix = os.path.splitext(parsed.path)[1] or ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        path = f.name
    urllib.request.urlretrieve(url, path)
    return path

def _resolve_image(source: str | None) -> str | None:
    """Return a local file path for *source* (URL, data URL, or existing file path)."""
    if not source:
        return None
    if source.startswith("http://") or source.startswith("https://"):
        return _download_image(source)
    if source.startswith("data:"):
        # data:image/png;base64,...
        m = re.match(r"data:([^;]+);base64,(.+)", source)
        if m:
            mime = m.group(1)
            ext = mimetypes.guess_extension(mime) or ".png"
            data = base64.b64decode(m.group(2))
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(data)
                return f.name
        raise ValueError("Unsupported data URL format")
    if os.path.isfile(source):
        return source
    raise FileNotFoundError(f"Image source not found: {source}")


def _subpixel_refinement(result: np.ndarray, max_loc: tuple[int, int]) -> float:
    """Parabolic interpolation around the correlation peak for sub-pixel accuracy."""
    x, y = max_loc
    h, w = result.shape
    y0, y1 = max(0, y - 1), min(h, y + 2)
    x0, x1 = max(0, x - 1), min(w, x + 2)
    neighborhood = result[y0:y1, x0:x1]
    if neighborhood.shape != (3, 3):
        return float(x)

    # Fit parabola in x-direction through the middle row
    a, b, c = neighborhood[1, 0], neighborhood[1, 1], neighborhood[1, 2]
    denom = 2 * (a - 2 * b + c)
    if abs(denom) < 1e-6:
        return float(x)
    dx = (a - c) / denom
    return float(float(x) + float(dx))


def _find_gap(bg_path: str, piece_path: str) -> dict[str, Any]:
    """Run OpenCV template matching to locate the puzzle gap.

    Returns a dict with ``gap_x`` and ``confidence``.
    """
    import cv2

    bg = cv2.imread(bg_path, cv2.IMREAD_UNCHANGED)
    piece = cv2.imread(piece_path, cv2.IMREAD_UNCHANGED)
    if bg is None:
        raise RuntimeError(f"Failed to load background image: {bg_path}")
    if piece is None:
        raise RuntimeError(f"Failed to load puzzle piece image: {piece_path}")

    # Convert to grayscale
    if bg.shape[2] == 4 if len(bg.shape) == 3 else False:
        bg_gray = cv2.cvtColor(bg[:, :, :3], cv2.COLOR_BGR2GRAY)
    else:
        bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)

    if piece.shape[2] == 4 if len(piece.shape) == 3 else False:
        piece_bgr = piece[:, :, :3]
        piece_alpha = piece[:, :, 3]
        piece_gray = cv2.cvtColor(piece_bgr, cv2.COLOR_BGR2GRAY)
        piece_mask = piece_alpha > 128
        piece_gray_masked = piece_gray.copy()
        piece_gray_masked[~piece_mask] = 0
    else:
        piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
        piece_gray_masked = piece_gray

    # Method 1: grayscale template matching
    result1 = cv2.matchTemplate(bg_gray, piece_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val1, _, max_loc1 = cv2.minMaxLoc(result1)
    gap1 = _subpixel_refinement(result1, max_loc1)

    # Method 2: edge-based matching
    bg_edges = cv2.Canny(bg_gray, 50, 150)
    piece_edges = cv2.Canny(piece_gray, 50, 150)
    result2 = cv2.matchTemplate(bg_edges, piece_edges, cv2.TM_CCOEFF_NORMED)
    _, max_val2, _, max_loc2 = cv2.minMaxLoc(result2)
    gap2 = _subpixel_refinement(result2, max_loc2)

    # Method 3: masked template matching (if alpha channel exists)
    if piece_gray_masked is not piece_gray:
        result3 = cv2.matchTemplate(bg_gray, piece_gray_masked, cv2.TM_CCOEFF_NORMED)
        _, max_val3, _, max_loc3 = cv2.minMaxLoc(result3)
        gap3 = _subpixel_refinement(result3, max_loc3)
    else:
        max_val3 = -1.0
        max_loc3 = (0, 0)
        gap3 = 0.0

    # Pick the best result
    candidates = [
        ("grayscale", float(max_val1), gap1),
        ("edge", float(max_val2), gap2),
        ("masked", float(max_val3), gap3),
    ]
    best = max(candidates, key=lambda c: c[1])

    return {
        "gap_x": round(best[2]),
        "gap_x_float": best[2],
        "confidence": best[1],
        "method": best[0],
        "all_methods": {
            name: {"confidence": conf, "gap_x": round(gx), "gap_x_float": gx}
            for name, conf, gx in candidates
        },
        "bg_size": {"width": bg.shape[1], "height": bg.shape[0]},
        "piece_size": {"width": piece.shape[1], "height": piece.shape[0]},
    }


# ---------------------------------------------------------------------------
# slider_find_gap
# ---------------------------------------------------------------------------


@register("slider_find_gap")
async def slider_find_gap(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Find the gap position in a slider CAPTCHA using OpenCV template matching.

    Parameters
    ----------
    bg_url / bg_path:
        Background image (the full CAPTCHA image with the empty slot).
    piece_url / piece_path:
        Puzzle piece image (the small draggable block).

    Returns the gap X coordinate, confidence score, and which method won.
    """
    bg_source = inp.get("bg_url") or inp.get("bg_path")
    piece_source = inp.get("piece_url") or inp.get("piece_path")
    if not bg_source or not piece_source:
        return ToolResult(
            output="slider_find_gap requires bg_url/bg_path and piece_url/piece_path",
            is_error=True,
        )

    tmp_files: list[str] = []
    try:
        # Download / resolve paths in a thread so we don't block the event loop
        bg_path, piece_path = await asyncio.to_thread(_resolve_images, bg_source, piece_source)
        if isinstance(bg_path, str) and bg_path not in (bg_source,):
            tmp_files.append(bg_path)
        if isinstance(piece_path, str) and piece_path not in (piece_source,):
            tmp_files.append(piece_path)

        result = await asyncio.to_thread(_find_gap, bg_path, piece_path)
        return ToolResult(
            output=json.dumps(result, ensure_ascii=False, indent=2),
            details=result,
        )
    except Exception as exc:
        return ToolResult(output=f"slider_find_gap error: {exc}", is_error=True)
    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass


def _resolve_images(bg_source: str, piece_source: str) -> tuple[str, str]:
    return _resolve_image(bg_source) or "", _resolve_image(piece_source) or ""


# ---------------------------------------------------------------------------
# slider_drag_cdp
# ---------------------------------------------------------------------------


def _build_cdp_drag_js(track: list[tuple[int, int, int]], end_x: int, end_y: int) -> str:
    """Build JS that dispatches drag via ``window.__openmimi_cdp_send``."""
    event_lines: list[str] = []
    event_lines.append(
        f"await cdp('Input.dispatchMouseEvent', {{type: 'mousePressed', x: {track[0][0]}, y: {track[0][1]}, button: 'left', clickCount: 1}});"
    )
    for px, py, d in track[1:]:
        if d > 5:
            event_lines.append(f"await new Promise(r => setTimeout(r, {d}));")
        event_lines.append(
            f"await cdp('Input.dispatchMouseEvent', {{type: 'mouseMoved', x: {px}, y: {py}, button: 'left'}});"
        )
    event_lines.append(
        f"await cdp('Input.dispatchMouseEvent', {{type: 'mouseReleased', x: {end_x}, y: {end_y}, button: 'left', clickCount: 1}});"
    )
    return (
        "(async () => {\n"
        "  const cdp = window.__openmimi_cdp_send;\n"
        + "\n".join(f"  {line}" for line in event_lines)
        + "\n  return {ok: true, points: "
        + str(len(track))
        + ", duration_ms: "
        + str(sum(d for _, _, d in track))
        + ", method: 'cdp'};\n"
        "})()"
    )


def _build_synthetic_drag_js(track: list[tuple[int, int, int]], end_x: int, end_y: int) -> str:
    """Build JS that dispatches synthetic PointerEvent/MouseEvent as fallback."""
    points_json = json.dumps([[px, py] for px, py, _ in track])
    return (
        "(async () => {\n"
        f"  const points = {points_json};\n"
        "  const startX = points[0][0];\n"
        "  const startY = points[0][1];\n"
        "  const endX = points[points.length - 1][0];\n"
        "  const endY = points[points.length - 1][1];\n"
        "  const target = document.elementFromPoint(startX, startY) || document.body;\n"
        "  target.dispatchEvent(new PointerEvent('pointerdown', {"
        "bubbles: true, cancelable: true, view: window, "
        "clientX: startX, clientY: startY, pointerType: 'mouse', button: 0, buttons: 1, isPrimary: true, pressure: 0.5"
        "}));\n"
        "  target.dispatchEvent(new MouseEvent('mousedown', {"
        "bubbles: true, cancelable: true, view: window, "
        "clientX: startX, clientY: startY, button: 0, buttons: 1"
        "}));\n"
        "  for (let i = 1; i < points.length; i++) {\n"
        "    const [x, y] = points[i];\n"
        "    document.dispatchEvent(new PointerEvent('pointermove', {"
        "bubbles: true, cancelable: true, view: window, "
        "clientX: x, clientY: y, pointerType: 'mouse', button: 0, buttons: 1, isPrimary: true, pressure: 0.5"
        "}));\n"
        "    document.dispatchEvent(new MouseEvent('mousemove', {"
        "bubbles: true, cancelable: true, view: window, "
        "clientX: x, clientY: y, button: 0, buttons: 1"
        "}));\n"
        "  }\n"
        "  const releaseOpts = {bubbles: true, cancelable: true, view: window, clientX: endX, clientY: endY, pointerType: 'mouse', button: 0, buttons: 0, isPrimary: true};\n"
        "  const mouseUpOpts = {bubbles: true, cancelable: true, view: window, clientX: endX, clientY: endY, button: 0, buttons: 0};\n"
        "  target.dispatchEvent(new PointerEvent('pointerup', releaseOpts));\n"
        "  document.dispatchEvent(new PointerEvent('pointerup', releaseOpts));\n"
        "  target.dispatchEvent(new MouseEvent('mouseup', mouseUpOpts));\n"
        "  document.dispatchEvent(new MouseEvent('mouseup', mouseUpOpts));\n"
        "  target.dispatchEvent(new MouseEvent('click', mouseUpOpts));\n"
        "  document.dispatchEvent(new MouseEvent('click', mouseUpOpts));\n"
        "  return {ok: true, points: points.length, method: 'synthetic'};\n"
        "})()"
    )


def _focus_agent_browser_window() -> bool:
    """Find and focus the Chromium window launched by agent-browser.

    Uses Windows APIs to locate the chrome.exe process whose parent is
    agent-browser, then brings that window to the foreground so that
    subsequent OS-level mouse events land on the browser rather than
    on whatever app happens to be in front.
    """
    try:
        import psutil
        import win32gui
        import win32process
        from ...utils.win_focus import force_set_foreground
    except ImportError:
        return False

    # Find agent-browser processes
    agent_pids = set()
    for p in psutil.process_iter(["pid", "name"]):
        name = p.info.get("name", "")
        if name and "agent-browser" in name.lower():
            agent_pids.add(p.info["pid"])

    if not agent_pids:
        return False

    # Find chrome.exe processes whose parent is agent-browser
    chrome_pids = set()
    for p in psutil.process_iter(["pid", "name", "ppid"]):
        name = p.info.get("name", "")
        ppid = p.info.get("ppid", 0)
        if name and name.lower() == "chrome.exe" and ppid in agent_pids:
            chrome_pids.add(p.info["pid"])

    if not chrome_pids:
        return False

    # Find the first visible window belonging to any of these chrome processes
    target_hwnd = None

    def _enum_callback(hwnd, _):
        nonlocal target_hwnd
        if target_hwnd:
            return
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in chrome_pids:
                target_hwnd = hwnd
        except Exception:
            pass

    win32gui.EnumWindows(_enum_callback, None)

    if target_hwnd:
        success, _ = force_set_foreground(target_hwnd)
        return success

    return False


@register("slider_drag_cdp")
async def slider_drag_cdp(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Drag a slider handle via CDP ``Input.dispatchMouseEvent``, with native-mouse fallback.

    Falls back to OS-level ``agent-browser mouse`` actions when the CDP bridge
    (``window.__openmimi_cdp_send``) is unavailable (e.g. new tabs opened via
    ``window.open``).  Native mouse moves the real cursor and is detected by
    the page as genuine input, unlike synthetic JS events which many CAPTCHA
    libraries ignore.

    Parameters
    ----------
    start_x, start_y:
        Initial cursor position (usually the handle centre).
    end_x, end_y:
        Target position (gap centre).
    steps:
        Number of intermediate mouse-moved events (default 80).
    delay_ms:
        Base delay between each CDP dispatch (default 15).
    humanize:
        Whether to use a human-like Bezier trajectory (default True).
    """
    start_x = int(inp.get("start_x", 0))
    start_y = int(inp.get("start_y", 0))
    end_x = int(inp.get("end_x", start_x))
    end_y = int(inp.get("end_y", start_y))
    steps = max(2, min(int(inp.get("steps", 80)), 500))
    delay_ms = max(1, min(int(inp.get("delay_ms", 15)), 500))
    humanize = inp.get("humanize", True)

    track = generate_trajectory(
        start_x, start_y, end_x, end_y, steps, delay_ms, humanize=humanize
    )

    # Try CDP first (browser-level, no OS focus needed)
    cdp_js = _build_cdp_drag_js(track, end_x, end_y)
    try:
        result = await engine._exec("eval", cdp_js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("ok"):
            return ToolResult(
                output=f"Slider drag completed ({result_value.get('method', 'cdp')}): {len(track)} points",
                details=result_value,
            )
    except Exception as exc:
        err_msg = str(exc).lower()
        # If the error is clearly about missing CDP bridge, fall through to native mouse
        if "cdp" not in err_msg and "function" not in err_msg and "__openmimi" not in err_msg:
            return ToolResult(output=f"slider_drag_cdp error: {exc}", is_error=True)

    # Fallback 1: native OS mouse via agent-browser mouse action.
    # This moves the real cursor and works even when synthetic JS events are ignored.
    # We must focus the browser window first, otherwise OS mouse events land elsewhere.
    try:
        focused = _focus_agent_browser_window()
        if focused:
            await asyncio.sleep(0.2)

        await engine._exec("mouse", "move", str(start_x), str(start_y))
        await asyncio.sleep(0.05)
        await engine._exec("mouse", "down", "left")
        await asyncio.sleep(0.05)

        for px, py, d in track[1:]:
            await engine._exec("mouse", "move", str(int(px)), str(int(py)))
            if d > 5:
                await asyncio.sleep(d / 1000.0)

        await asyncio.sleep(0.05)
        await engine._exec("mouse", "up", "left")
        await asyncio.sleep(0.1)

        return ToolResult(
            output=f"Slider drag completed (native_mouse): {len(track)} points",
            details={"ok": True, "points": len(track), "duration_ms": sum(d for _, _, d in track), "method": "native_mouse", "focused": focused},
        )
    except Exception as exc2:
        # Fallback 2: synthetic JS events (last resort)
        synthetic_js = _build_synthetic_drag_js(track, end_x, end_y)
        try:
            result = await engine._exec("eval", synthetic_js, "--json")
            data = engine._parse_data(result.stdout)
            result_value = data.get("result") if isinstance(data, dict) else None
            if isinstance(result_value, dict) and result_value.get("ok"):
                return ToolResult(
                    output=f"Slider drag completed ({result_value.get('method', 'synthetic')}): {len(track)} points",
                    details=result_value,
                )
            return ToolResult(
                output=f"slider_drag_cdp synthetic fallback completed with unexpected result: {json.dumps(result_value, ensure_ascii=False)[:500]}",
                details={"raw": result_value},
            )
        except Exception as exc3:
            return ToolResult(
                output=f"slider_drag_cdp error (CDP, native mouse, and synthetic all failed): {exc3}",
                is_error=True,
            )


# ---------------------------------------------------------------------------
# slider_find_gap_vision
# ---------------------------------------------------------------------------


def _read_image_file(path: str) -> tuple[bytes, str]:
    """Read image file and infer media type from extension."""
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return data, "image/jpeg"
    if ext == ".webp":
        return data, "image/webp"
    return data, "image/png"


def _get_image_size(img_bytes: bytes) -> tuple[int, int] | None:
    """Return (width, height) from image bytes, or None if PIL unavailable."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(img_bytes)) as img:
            return img.size
    except Exception:
        return None


async def _screenshot_element(engine: "AgentBrowserTool", selector: str) -> tuple[bytes, str] | None:
    """Screenshot a specific element by CSS selector. Returns (bytes, media_type)."""
    tmp_path = tempfile.mktemp(suffix=".png")
    try:
        await engine._exec("screenshot", selector, tmp_path, "--json")
        if not os.path.exists(tmp_path):
            return None
        with open(tmp_path, "rb") as f:
            return f.read(), "image/png"
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def _call_vision_llm(
    images: list[tuple[bytes, str]],
    prompt: str,
    api_key: str,
    model: str,
    base_url: str | None,
    provider: str,
) -> str:
    """Send images + prompt to vision LLM and return text response."""
    from ...llm import AnthropicClient, OpenAIChatClient

    content_blocks: list[dict[str, Any]] = []
    for img_bytes, media_type in images:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        content_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        })
    content_blocks.append({"type": "text", "text": prompt})

    messages = [{"role": "user", "content": content_blocks}]

    if provider.lower() == "anthropic":
        client = AnthropicClient(api_key=api_key, model=model, base_url=base_url)
    else:
        client = OpenAIChatClient(api_key=api_key, model=model, base_url=base_url)

    result = await client.create(
        system="You are a precise visual analysis assistant. Respond only with valid JSON.",
        messages=messages,
        tools=[],
        max_tokens=512,
    )

    text_parts: list[str] = []
    for block in result.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))
    return "\n".join(text_parts)


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Extract JSON object from LLM response text."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict) and "gap_x" in data:
            return data
    except json.JSONDecodeError:
        pass

    m = re.search(r'"gap_x"\s*:\s*(\d+)', text)
    if m:
        return {"gap_x": int(m.group(1)), "confidence": 0.5, "reasoning": "extracted via regex fallback"}

    return None


@register("slider_find_gap_vision")
async def slider_find_gap_vision(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Find slider CAPTCHA gap using a vision LLM instead of OpenCV.

    Use this when OpenCV template matching fails or when higher accuracy is needed.
    Only invoked when a slider CAPTCHA is actually detected, saving tokens.

    Parameters
    ----------
    bg_url / bg_path / bg_selector:
        Background image source. If selector is provided, a screenshot of that element is taken.
    piece_url / piece_path / piece_selector:
        Optional puzzle piece image to help the model locate the gap.
    api_key:
        LLM API key.
    model:
        Model name, e.g. 'gpt-4o', 'claude-3-5-sonnet-20241022'.
    base_url:
        Optional API base URL for OpenAI-compatible proxies.
    provider:
        'openai' or 'anthropic' (default 'openai').
    prompt:
        Optional custom prompt override.
    """
    api_key = inp.get("api_key")
    model = inp.get("model")
    if not api_key or not model:
        return ToolResult(
            output="slider_find_gap_vision requires api_key and model",
            is_error=True,
        )

    base_url = inp.get("base_url") or None
    provider = inp.get("provider", "openai")
    custom_prompt = inp.get("prompt")

    tmp_files: list[str] = []
    images: list[tuple[bytes, str]] = []

    try:
        # Resolve background image
        bg_source = inp.get("bg_url") or inp.get("bg_path")
        bg_selector = inp.get("bg_selector")
        if bg_source:
            bg_path = await asyncio.to_thread(_resolve_image, bg_source)
            if bg_path and bg_path != bg_source:
                tmp_files.append(bg_path)
            if bg_path:
                img_bytes, media_type = _read_image_file(bg_path)
                images.append((img_bytes, media_type))
        elif bg_selector:
            screenshot = await _screenshot_element(engine, bg_selector)
            if screenshot:
                images.append(screenshot)
            else:
                return ToolResult(output=f"Failed to screenshot element: {bg_selector}", is_error=True)
        else:
            return ToolResult(
                output="slider_find_gap_vision requires bg_url/bg_path or bg_selector",
                is_error=True,
            )

        # Resolve piece image (optional)
        piece_source = inp.get("piece_url") or inp.get("piece_path")
        piece_selector = inp.get("piece_selector")
        if piece_source:
            piece_path = await asyncio.to_thread(_resolve_image, piece_source)
            if piece_path and piece_path != piece_source:
                tmp_files.append(piece_path)
            if piece_path:
                img_bytes, media_type = _read_image_file(piece_path)
                images.append((img_bytes, media_type))
        elif piece_selector:
            screenshot = await _screenshot_element(engine, piece_selector)
            if screenshot:
                images.append(screenshot)

        if not images:
            return ToolResult(output="No images resolved for vision analysis", is_error=True)

        # Build prompt
        bg_size = _get_image_size(images[0][0])
        width = bg_size[0] if bg_size else "unknown"
        height = bg_size[1] if bg_size else "unknown"

        if custom_prompt:
            prompt = custom_prompt
        else:
            prompt = (
                f"You are analyzing a slider CAPTCHA puzzle.\n\n"
                f"The attached background image is {width}x{height} pixels. "
                f"It shows a scene with a missing puzzle-piece shaped gap (slot). "
                f"Identify the horizontal center (X-coordinate) of this gap, "
                f"measured in pixels from the left edge of the image.\n\n"
            )
            if len(images) > 1:
                prompt += (
                    "There is also a puzzle piece image. This piece should fit into the gap. "
                    "Use it to help pinpoint the exact gap location.\n\n"
                )
            prompt += (
                f"Return ONLY a JSON object with no markdown formatting:\n"
                f'{{"gap_x": <integer 0-{width if isinstance(width, int) else "max"}>, '
                f'"confidence": <0.0 to 1.0>, "reasoning": "<one sentence>"}}'
            )

        # Call vision LLM
        llm_text = await _call_vision_llm(
            images, prompt, api_key, model, base_url, provider
        )

        # Parse result
        parsed = _extract_json_from_text(llm_text)
        if parsed is None:
            return ToolResult(
                output=f"Vision LLM did not return valid gap_x. Raw response:\n{llm_text[:500]}",
                is_error=True,
            )

        gap_x = parsed.get("gap_x")
        if gap_x is None:
            return ToolResult(
                output=f"Vision LLM response missing gap_x. Raw response:\n{llm_text[:500]}",
                is_error=True,
            )

        return ToolResult(
            output=json.dumps(parsed, ensure_ascii=False, indent=2),
            details={
                "gap_x": gap_x,
                "gap_x_float": float(gap_x),
                "confidence": parsed.get("confidence"),
                "reasoning": parsed.get("reasoning"),
                "method": "vision",
                "llm_raw": llm_text[:1000],
                "bg_size": {"width": width, "height": height} if bg_size else None,
            },
        )

    except Exception as exc:
        return ToolResult(output=f"slider_find_gap_vision error: {exc}", is_error=True)
    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
