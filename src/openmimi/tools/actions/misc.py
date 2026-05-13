"""Miscellaneous action handlers — step 8 of the actions/ migration.

Covers a grab-bag of remaining actions that don't fit the earlier family
modules: form inputs (select/upload/download), JS execution (eval/batch),
raw input/window control (drag/mouse/focus/set_viewport), and CDP-based
emulation overrides (emulate_device/set_timezone/set_locale/set_geolocation).

Each handler is a verbatim port of the corresponding ``_do_*`` method on
``AgentBrowserTool``: ``self.`` → ``engine.``, no behaviour changes. The
``close`` action is intentionally NOT migrated here — it has a dedicated
fast path in ``AgentBrowserTool.__call__`` that runs before auto-start, so
it lives on the main class.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ..result import ToolResult
from . import register

if TYPE_CHECKING:
    from ..agent_browser import AgentBrowserTool


# ---------------------------------------------------------------------------
# Form inputs
# ---------------------------------------------------------------------------


@register("select")
async def select(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    options = inp.get("options", [])
    if not options:
        return ToolResult(output="select requires 'options' array", is_error=True)
    selector = ref or target_text
    if not selector:
        return ToolResult(output="select requires 'ref' or 'target_text'", is_error=True)
    args = ["select", selector] + [str(o) for o in options] + ["--json"]
    result = await engine._exec(*args)
    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Selected {options} on {selector}\n{result.stdout[:1000]}",
        base64_image=image,
    )


@register("upload")
async def upload(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    file_path = inp.get("file_path")
    if not file_path:
        return ToolResult(output="upload requires 'file_path'", is_error=True)
    selector = ref or target_text
    if not selector:
        return ToolResult(output="upload requires 'ref' or 'target_text'", is_error=True)
    result = await engine._exec("upload", selector, file_path, "--json")
    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Uploaded {file_path} to {selector}\n{result.stdout[:1000]}",
        base64_image=image,
    )


@register("download")
async def download(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    save_path = inp.get("file_path") or inp.get("save_path")
    if not save_path:
        return ToolResult(output="download requires 'file_path' (save location)", is_error=True)
    selector = ref or target_text
    if not selector:
        return ToolResult(output="download requires 'ref' or 'target_text'", is_error=True)
    result = await engine._exec("download", selector, save_path, "--json")
    image = await engine._take_screenshot()
    return ToolResult(
        output=f"Downloaded to {save_path} from {selector}\n{result.stdout[:1000]}",
        base64_image=image,
    )


# ---------------------------------------------------------------------------
# JS execution
# ---------------------------------------------------------------------------


@register("eval")
async def eval_js(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    js = inp.get("js") or inp.get("js_code") or ""
    if not js.strip():
        return ToolResult(
            output="eval requires non-empty 'js' field", is_error=True
        )
    result = await engine._exec("eval", js, "--json")
    raw = engine._parse_json(result.stdout)
    if not isinstance(raw, dict):
        return ToolResult(
            output=f"Invalid eval response: {result.stdout[:200]}",
            is_error=True,
        )
    if raw.get("success") is False:
        err = raw.get("error") or "eval failed"
        return ToolResult(output=f"Eval error: {err}", is_error=True)
    data = raw.get("data", {})
    result_value = data.get("result") if isinstance(data, dict) else None
    if result_value is None or result_value == {} or result_value == []:
        return ToolResult(
            output=(
                f"eval result is empty/null. "
                f"Make sure your JS ends with an explicit return value "
                f"inside an IIFE like (() => {{ ...; return value; }})(). "
                f"Raw data: {json.dumps(data, ensure_ascii=False)[:500]}"
            )
        )
    return ToolResult(output=json.dumps(result_value, ensure_ascii=False, indent=2))


@register("batch")
async def batch(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    steps = inp.get("steps", [])
    if not isinstance(steps, list) or not steps:
        return ToolResult(
            output="batch requires non-empty 'steps' array of strings",
            is_error=True,
        )
    normalized: list[str] = []
    for idx, step in enumerate(steps):
        if isinstance(step, list):
            # Tolerate token arrays (agent-browser stdin JSON shape).
            step = " ".join(str(t) for t in step)
        if not isinstance(step, str) or not step.strip():
            return ToolResult(
                output=(
                    f"batch step {idx} must be a non-empty string (got {type(step).__name__}: {step!r}). "
                    f"Each step is a full agent-browser command, e.g. 'mouse move 100 200'."
                ),
                is_error=True,
            )
        normalized.append(step)
    args = ["batch", "--bail", "--json"] + normalized
    result = await engine._exec(*args)
    data = engine._parse_data(result.stdout)
    image = await engine._take_screenshot()
    return ToolResult(
        output=json.dumps(data, ensure_ascii=False, indent=2)[:4000],
        base64_image=image,
    )


# ---------------------------------------------------------------------------
# Raw input / window control
# ---------------------------------------------------------------------------


@register("drag")
async def drag(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    ref = inp.get("ref")
    target_text = inp.get("target_text")
    to_ref = inp.get("to_ref")
    to_target_text = inp.get("to_target_text")
    if ref and to_ref:
        await engine._exec("drag", ref, to_ref, "--json")
    elif target_text and to_target_text:
        # agent-browser find syntax: find <locator> <value> <action> ...
        # Drag target is an element ref/selector, not text, so this path
        # is best-effort. Prefer refs for drag.
        await engine._exec(
            "find", "text", target_text, "drag", to_target_text, "--json"
        )
    else:
        return ToolResult(
            output="drag requires 'ref'+'to_ref' or 'target_text'+'to_target_text'"
        )
    image = await engine._take_screenshot()
    return ToolResult(output="Dragged element", base64_image=image)


@register("mouse")
async def mouse(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    mouse_action = inp.get("mouse_action", "move")
    if mouse_action == "move":
        x = inp.get("x", 0)
        y = inp.get("y", 0)
        await engine._exec("mouse", "move", str(x), str(y), "--json")
    elif mouse_action == "down":
        btn = inp.get("button", "left")
        await engine._exec("mouse", "down", btn, "--json")
    elif mouse_action == "up":
        btn = inp.get("button", "left")
        await engine._exec("mouse", "up", btn, "--json")
    elif mouse_action == "wheel":
        dy = inp.get("amount", 0)
        dx = inp.get("x", 0)
        await engine._exec("mouse", "wheel", str(dy), str(dx), "--json")
    else:
        return ToolResult(output=f"Unknown mouse_action: {mouse_action}")
    image = await engine._take_screenshot()
    return ToolResult(output=f"Mouse {mouse_action}", base64_image=image)


@register("focus")
async def focus(engine: "AgentBrowserTool", _inp: dict[str, Any]) -> ToolResult:
    """Bring the browser window to the foreground using win32gui."""
    try:
        import win32gui
    except ImportError:
        return ToolResult(output="win32gui not available", is_error=True)

    try:
        result = await engine._exec(
            "eval",
            "(() => ({title: document.title, href: window.location.href}))()",
            "--json",
        )
        raw = engine._parse_json(result.stdout)
        page_info: dict[str, str] = {}
        if isinstance(raw, dict) and raw.get("success") is True:
            data = raw.get("data", {})
            res = data.get("result") if isinstance(data, dict) else {}
            if isinstance(res, dict):
                page_info = res
    except Exception:
        page_info = {}

    title_hint = page_info.get("title", "").strip().lower()
    url_hint = page_info.get("href", "").strip().lower()
    domain = ""
    if url_hint:
        try:
            domain = urlparse(url_hint).netloc.lower()
        except Exception:
            pass

    def _enum(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            wt = win32gui.GetWindowText(hwnd).lower()
            extra.append((hwnd, wt))
        return True

    candidates: list[tuple[int, str]] = []
    win32gui.EnumWindows(_enum, candidates)

    best: tuple[int, str, int] | None = None
    for hwnd, wt in candidates:
        score = 0
        if title_hint and title_hint in wt:
            score += 3
        if domain and domain in wt:
            score += 2
        if url_hint and url_hint in wt:
            score += 2
        if "chromium" in wt or "chrome" in wt:
            score += 1
        if score > 0 and (best is None or score > best[2]):
            best = (hwnd, wt, score)

    if best is None:
        return ToolResult(
            output="Could not find a visible browser window to focus",
            is_error=True,
        )

    hwnd, wt, _score = best
    from ...utils.win_focus import force_set_foreground

    ok, method = force_set_foreground(hwnd)
    if not ok:
        return ToolResult(
            output=(
                f"Failed to focus browser window '{wt}' even after foreground-lock "
                f"workarounds (method={method})."
            ),
            is_error=True,
        )
    return ToolResult(output=f"Browser window focused ({method}): {wt}")


@register("set_viewport")
async def set_viewport(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Resize the browser viewport to the specified width and height."""
    width = inp.get("width")
    height = inp.get("height")
    if width is None or height is None:
        return ToolResult(output="set_viewport requires 'width' and 'height'", is_error=True)
    js = f"""
    (() => {{
        window.resizeTo({int(width)}, {int(height)});
        return {{width: window.innerWidth, height: window.innerHeight, screenWidth: window.screen.width, screenHeight: window.screen.height}};
    }})()
    """
    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        output = json.dumps(result_value, ensure_ascii=False, indent=2)[:2000]
        return ToolResult(output=f"Viewport set. {output}")
    except Exception as exc:
        return ToolResult(output=f"set_viewport failed: {exc}", is_error=True)


# ---------------------------------------------------------------------------
# CDP emulation overrides
# ---------------------------------------------------------------------------


_DEVICE_PRESETS: dict[str, dict[str, Any]] = {
    "iPhone 14": {
        "width": 390,
        "height": 844,
        "deviceScaleFactor": 3,
        "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "mobile": True,
        "touch": True,
    },
    "iPhone 14 Pro Max": {
        "width": 430,
        "height": 932,
        "deviceScaleFactor": 3,
        "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "mobile": True,
        "touch": True,
    },
    "Pixel 7": {
        "width": 412,
        "height": 915,
        "deviceScaleFactor": 2.625,
        "userAgent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
        "mobile": True,
        "touch": True,
    },
    "iPad Mini": {
        "width": 768,
        "height": 1024,
        "deviceScaleFactor": 2,
        "userAgent": "Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "mobile": True,
        "touch": True,
    },
    "reset": {
        "width": 1280,
        "height": 800,
        "deviceScaleFactor": 1,
        "userAgent": "",
        "mobile": False,
        "touch": False,
    },
}


@register("emulate_device")
async def emulate_device(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Emulate a mobile device via CDP or JS fallback."""
    device_name = inp.get("device_name", "iPhone 14")
    preset = _DEVICE_PRESETS.get(device_name, _DEVICE_PRESETS["iPhone 14"])
    width = int(preset["width"])
    height = int(preset["height"])
    dpr = float(preset["deviceScaleFactor"])
    ua = preset["userAgent"]
    mobile = preset["mobile"]
    touch = preset["touch"]

    # Try CDP Emulation.setDeviceMetricsOverride first
    touch_js = (
        f"await window.__openmimi_cdp_send('Emulation.setTouchEmulationEnabled', "
        f"{{enabled: {str(touch).lower()}, maxTouchPoints: 5}});"
        if touch
        else ""
    )
    cdp_js = f"""
    (async () => {{
        try {{
            await window.__openmimi_cdp_send('Emulation.setDeviceMetricsOverride', {{
                width: {width},
                height: {height},
                deviceScaleFactor: {dpr},
                mobile: {str(mobile).lower()},
            }});
            {'await window.__openmimi_cdp_send("Emulation.setUserAgentOverride", {userAgent: ' + json.dumps(ua) + '});' if ua else ''}
            {touch_js}
            return {{ok: true, method: 'cdp', device: {json.dumps(device_name)}}};
        }} catch (e) {{
            return {{error: e.message, note: 'CDP emulation failed'}};
        }}
    }})()
    """
    try:
        result = await engine._exec("eval", cdp_js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("ok"):
            image = await engine._take_screenshot()
            return ToolResult(
                output=f"Emulating {device_name}: {width}x{height} @ {dpr}x DPR",
                base64_image=image,
                details={"device": device_name, "width": width, "height": height, "dpr": dpr},
            )
    except Exception:
        pass

    # Fallback: JS-only viewport + UA override
    js = f"""
    (() => {{
        window.resizeTo({width}, {height});
        {'Object.defineProperty(navigator, "userAgent", {get: () => ' + json.dumps(ua) + ', configurable: true});' if ua else ''}
        return {{
            width: window.innerWidth,
            height: window.innerHeight,
            device: {json.dumps(device_name)},
            method: 'js_fallback',
        }};
    }})()
    """
    try:
        result = await engine._exec("eval", js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        image = await engine._take_screenshot()
        return ToolResult(
            output=f"Emulating {device_name} (JS fallback): {json.dumps(result_value, ensure_ascii=False)[:500]}",
            base64_image=image,
            details={"device": device_name, "width": width, "height": height, "dpr": dpr},
        )
    except Exception as exc:
        return ToolResult(output=f"emulate_device failed: {exc}", is_error=True)


@register("set_timezone")
async def set_timezone(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Set browser timezone via CDP Emulation.setTimezoneOverride."""
    tz = str(inp.get("timezone", ""))
    cdp_js = (
        "(async () => {\n"
        "  try {\n"
        "    await window.__openmimi_cdp_send('Emulation.setTimezoneOverride', {timezoneId: " + json.dumps(tz) + "});\n"
        "    return {ok: true, timezone: " + json.dumps(tz) + "};\n"
        "  } catch (e) {\n"
        "    return {error: e.message};\n"
        "  }\n"
        "})()"
    )
    try:
        result = await engine._exec("eval", cdp_js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("error"):
            return ToolResult(output=f"set_timezone failed: {result_value['error']}", is_error=True)
        return ToolResult(output=f"Timezone set to {tz!r}" if tz else "Timezone reset to default")
    except Exception as exc:
        return ToolResult(output=f"set_timezone error: {exc}", is_error=True)


@register("set_locale")
async def set_locale(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Set browser locale via CDP Emulation.setLocaleOverride."""
    loc = str(inp.get("locale", ""))
    cdp_js = (
        "(async () => {\n"
        "  try {\n"
        "    await window.__openmimi_cdp_send('Emulation.setLocaleOverride', {locale: " + json.dumps(loc) + "});\n"
        "    return {ok: true, locale: " + json.dumps(loc) + "};\n"
        "  } catch (e) {\n"
        "    return {error: e.message};\n"
        "  }\n"
        "})()"
    )
    try:
        result = await engine._exec("eval", cdp_js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("error"):
            return ToolResult(output=f"set_locale failed: {result_value['error']}", is_error=True)
        return ToolResult(output=f"Locale set to {loc!r}" if loc else "Locale reset to default")
    except Exception as exc:
        return ToolResult(output=f"set_locale error: {exc}", is_error=True)


@register("set_geolocation")
async def set_geolocation(engine: "AgentBrowserTool", inp: dict[str, Any]) -> ToolResult:
    """Set browser geolocation via CDP Emulation.setGeolocationOverride."""
    lat = inp.get("latitude")
    lon = inp.get("longitude")
    acc = inp.get("accuracy", 100)
    if lat is None or lon is None:
        # Clear override
        cdp_js = (
            "(async () => {\n"
            "  try {\n"
            "    await window.__openmimi_cdp_send('Emulation.clearGeolocationOverride');\n"
            "    return {ok: true, cleared: true};\n"
            "  } catch (e) {\n"
            "    return {error: e.message};\n"
            "  }\n"
            "})()"
        )
        try:
            await engine._exec("eval", cdp_js, "--json")
            return ToolResult(output="Geolocation override cleared")
        except Exception as exc:
            return ToolResult(output=f"clear_geolocation error: {exc}", is_error=True)
    cdp_js = (
        "(async () => {\n"
        "  try {\n"
        "    await window.__openmimi_cdp_send('Emulation.setGeolocationOverride', {\n"
        "      latitude: " + str(float(lat)) + ",\n"
        "      longitude: " + str(float(lon)) + ",\n"
        "      accuracy: " + str(float(acc)) + "\n"
        "    });\n"
        "    return {ok: true, lat: " + str(float(lat)) + ", lon: " + str(float(lon)) + "};\n"
        "  } catch (e) {\n"
        "    return {error: e.message};\n"
        "  }\n"
        "})()"
    )
    try:
        result = await engine._exec("eval", cdp_js, "--json")
        data = engine._parse_data(result.stdout)
        result_value = data.get("result") if isinstance(data, dict) else None
        if isinstance(result_value, dict) and result_value.get("error"):
            return ToolResult(output=f"set_geolocation failed: {result_value['error']}", is_error=True)
        return ToolResult(output=f"Geolocation set to ({lat}, {lon}) ±{acc}m")
    except Exception as exc:
        return ToolResult(output=f"set_geolocation error: {exc}", is_error=True)
