"""Static description text + JSON input schema for AgentBrowserTool.

These are split out of ``agent_browser.py`` because they are pure data with
no dependency on instance state, and together accounted for ~400 lines of
the god-class file (#4-lite step 9). The runtime imports
:data:`TOOL_DESCRIPTION` and calls :func:`build_input_schema` to fill the
``description`` and ``input_schema`` fields of the tool params dict.

The description string is a single block of plain text consumed by the LLM
as part of the tool catalog. The schema dict mirrors what the Anthropic
``tools`` parameter expects (see Anthropic tool-use docs).
"""
from __future__ import annotations

from typing import Any


TOOL_DESCRIPTION = (
    "Operate a Chromium browser via agent-browser (Rust CLI). "
    "Core workflow: 1) Call action='snapshot' to get an accessibility tree with @eN refs; "
    "2) Use those refs in action='click' / 'right_click' / 'double_click' / 'type' / 'fill' / 'react_fill' / 'hover' / 'drag' via the 'ref' field; "
    "3) Call action='screenshot' when visual verification is needed. "
    "If no ref is known, use 'target_text' for semantic text matching. "
    "Navigation: action='navigate' with 'url', or 'back' / 'forward' / 'reload'. "
    "Tabs: action='tab_list' or action='tab_switch' with 'tab_index' (1-based). "
    "Checkbox: use action='check' or 'uncheck' with 'ref' (never click checkboxes). "
    "Dropdown: use action='select' with 'ref' and 'value'. "
    "File upload: use action='upload' with 'ref' and 'file_path'. "
    "File download: use action='download' with 'ref' and 'save_path'. "
    "Drag and drop: action='drag' with 'ref'+'to_ref' or 'target_text'+'to_target_text'. "
    "Low-level mouse control: action='mouse' with 'mouse_action' (move/down/up/wheel). "
    "Use mouse sequences (move -> down -> move -> up) for interactions that standard click "
    "cannot handle, such as dragging sliders, drawing, or any press-and-hold gesture. "
    "Window focus: action='focus' brings the browser window to the foreground (useful "
    "before OS-level mouse actions like computer.mouse_drag on CAPTCHAs). "
    "Scroll into view: action='scroll_into_view' with 'ref' or 'target_text' brings an element into the viewport. "
    "Page source: action='page_source' returns the raw HTML of the current page. "
    "Get URL: action='get_url' returns the current page URL. "
    "Get title: action='get_title' returns the current page title. "
    "Get attribute: action='get_attribute' with 'ref' or 'target_text' and 'attribute_name' reads a DOM attribute (e.g. href, src, data-id). "
    "Set attribute: action='set_attribute' with 'ref' or 'target_text', 'attribute_name', and 'attribute_value' writes a DOM attribute. "
    "Get property: action='get_property' with 'ref' or 'target_text' and 'property_name' reads a JS property (e.g. value, checked, innerText). "
    "Wait for navigation: action='wait_for_navigation' waits for the URL to change after a click or form submission. "
    "Wait for network idle: action='wait_for_network_idle' waits until no fetch/XHR requests are active for idle_duration_ms (default 2000). "
    "Element coordinates: action='get_box' with 'ref' or 'target_text' returns the "
    "element's bounding box (x, y, width, height) for OS-level mouse coordination. "
    "Visibility check: action='is_visible' with 'ref' or 'target_text' returns whether "
    "the element is present and visible (not display:none, visibility:hidden, or zero size). "
    "Visual locate: action='visual_locate' with 'template_path' uses OpenCV template "
    "matching on the page screenshot to find elements by visual appearance. Optional "
    "'click'=true to click on the matched region. Useful for canvas UIs, custom icons, "
    "or when DOM selectors are unreliable.\n"
    "Human-like scroll: action='human_scroll' performs scroll in multiple small steps with "
    "random pauses between each step, simulating human reading behavior and reducing bot detection.\n"
    "Scroll until found: action='scroll_until' scrolls in steps until an element (ref/target_text) or text appears. "
    "Useful for infinite scroll and long forms. Parameters: direction, step_pixels, timeout_ms, interval_ms.\n"
    "Dynamic content: action='wait_for' with 'ref', 'target_text', or 'text' waits until "
    "the element or text appears on the page (useful for React/Vue SPAs that render lazily). "
    "wait_for_disappear: action='wait_for_disappear' with 'ref', 'target_text', or 'text' waits until "
    "the element or text is no longer present (useful for loading spinners, CAPTCHA modals, overlays). "
    "Network debugging: action='network_log' with optional 'duration_ms' and 'filter' "
    "intercepts fetch/XHR requests and captures response status codes and bodies to discover hidden API endpoints. "
    "Network modification: action='network_modify' with 'modify_action' can inject headers, "
    "block URLs by pattern, mock responses (fetch + XHR), or override User-Agent. Use this to bypass "
    "anti-bot detection or inject auth tokens into API requests. "
    "React form fill: action='react_fill' with 'ref'/'target_text' and 'value' uses the "
    "HTMLInputElement.prototype.value setter + dispatchEvent(input/change) pattern, which "
    "is required for React/Vue controlled inputs that ignore direct value assignment. "
    "Use react_fill instead of fill on React SPAs.\n"
    "Extract: action='extract' with 'instruction' retrieves structured data: 'get text', "
    "'headings', 'links', 'forms', 'tables', 'metadata', 'images'.\n"
    "Storage: action='storage' with 'storage_action' (get/set/delete/clear) and 'storage_type' "
    "(localStorage, sessionStorage, cookies) to read or modify browser storage. "
    "PDF: action='pdf' with 'file_path' saves the current page as a PDF. "
    "Console: action='console' returns recent browser console logs (errors, warnings, info). "
    "Clear data: action='clear_cache' wipes cookies, localStorage, and sessionStorage. "
    "Viewport: action='set_viewport' with 'width' and 'height' resizes the browser window. "
    "Device emulation: action='emulate_device' with 'device_name' (iPhone 14, Pixel 7, iPad Mini, reset) "
    "sets viewport, DPR, and user agent for mobile testing.\n"
    "Timezone: action='set_timezone' with 'timezone' (e.g. 'Asia/Shanghai') overrides browser timezone via CDP. Pass empty string to reset.\n"
    "Locale: action='set_locale' with 'locale' (e.g. 'zh-CN') overrides browser locale via CDP. Pass empty string to reset.\n"
    "Geolocation: action='set_geolocation' with 'latitude', 'longitude', and optional 'accuracy' overrides GPS location via CDP. Omit coords to clear.\n"
    "CDP raw access: action='cdp' with 'cdp_method' and optional 'cdp_params' sends arbitrary "
    "Chrome DevTools Protocol commands via window.__openmimi_cdp_send. Use as an escape hatch "
    "for CDP features not covered by other actions.\n"
    "Session persistence: action='save_session' with 'file_path' persists cookies/storage; "
    "action='load_session' with 'file_path' restores them to avoid repeated logins. "
    "Persistent profile: pass user_data_dir when creating the tool to reuse cookies, cache, "
    "and extensions across sessions (more robust than save_session/load_session). "
    "Proxy: pass proxy='http://host:port' when creating the tool to route traffic through a proxy. "
    "Slow motion: pass slow_mo_ms when creating the tool to add a randomized delay after each action, "
    "making automation less detectable by bot detection systems (e.g. slow_mo_ms=200).\n"
    "For multi-step atomic execution, use action='batch' with 'steps'."
)


_ACTION_ENUM: list[str] = [
    "navigate",
    "back",
    "forward",
    "reload",
    "snapshot",
    "click",
    "right_click",
    "double_click",
    "check",
    "uncheck",
    "type",
    "fill",
    "react_fill",
    "press",
    "key_combo",
    "hover",
    "scroll",
    "human_scroll",
    "scroll_until",
    "screenshot",
    "extract",
    "select",
    "upload",
    "download",
    "tab_list",
    "tab_switch",
    "tab_new",
    "tab_close",
    "wait",
    "eval",
    "batch",
    "close",
    "drag",
    "mouse",
    "focus",
    "clipboard",
    "get_box",
    "is_visible",
    "visual_locate",
    "wait_for",
    "wait_for_disappear",
    "network_log",
    "network_modify",
    "storage",
    "pdf",
    "console",
    "clear_cache",
    "set_viewport",
    "save_session",
    "load_session",
    "scroll_into_view",
    "page_source",
    "get_url",
    "get_title",
    "get_attribute",
    "set_attribute",
    "get_property",
    "wait_for_navigation",
    "wait_for_network_idle",
    "emulate_device",
    "set_timezone",
    "set_locale",
    "set_geolocation",
    "cdp",
]


def build_input_schema() -> dict[str, Any]:
    """Return the JSON Schema dict consumed by Anthropic ``tools`` param.

    Returned fresh on each call so callers may safely mutate it (e.g. for
    per-instance customisation in the future) without polluting a shared
    module-level dict.
    """
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_ACTION_ENUM),
                "description": "The browser action to perform.",
            },
            "ref": {
                "type": "string",
                "description": "Element reference from snapshot, e.g. @e3. Preferred over target_text.",
            },
            "target_text": {
                "type": "string",
                "description": "Visible text to locate the element when ref is unavailable.",
            },
            "force": {
                "type": "boolean",
                "description": "Use low-level mouse down/up sequence instead of standard click. Useful for React SPAs and elements that ignore synthetic click events.",
            },
            "value": {
                "type": "string",
                "description": "Text to type or fill.",
            },
            "url": {
                "type": "string",
                "description": "URL for navigate or tab_new.",
            },
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "description": "Scroll direction.",
            },
            "amount": {
                "type": "integer",
                "description": "Scroll amount in pixels (default 500).",
            },
            "step_pixels": {
                "type": "integer",
                "description": "Scroll step size in pixels for action='scroll_until' (default 500).",
            },
            "key": {
                "type": "string",
                "description": "Key to press, e.g. Enter, Escape, Tab.",
            },
            "tab_index": {
                "type": "integer",
                "description": "1-based tab index for tab_switch or tab_close.",
            },
            "milliseconds": {
                "type": "integer",
                "description": "Wait time in milliseconds.",
            },
            "js": {
                "type": "string",
                "description": "JavaScript code to evaluate.",
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of agent-browser commands for batch action.",
            },
            "instruction": {
                "type": "string",
                "description": "For extract: what to extract from the page (e.g. 'get text').",
            },
            "to_ref": {
                "type": "string",
                "description": "Target element reference for drag action, e.g. @e5.",
            },
            "to_target_text": {
                "type": "string",
                "description": "Target element visible text for drag action.",
            },
            "mouse_action": {
                "type": "string",
                "enum": ["move", "down", "up", "wheel"],
                "description": "Mouse subcommand for action='mouse'.",
            },
            "x": {
                "type": "integer",
                "description": "X coordinate for mouse move.",
            },
            "y": {
                "type": "integer",
                "description": "Y coordinate for mouse move.",
            },
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button for down/up (default left).",
            },
            "clipboard_action": {
                "type": "string",
                "enum": ["read", "write", "copy", "paste"],
                "description": "Clipboard subcommand for action='clipboard'.",
            },
            "clipboard_text": {
                "type": "string",
                "description": "Text to write to clipboard (for clipboard_action='write').",
            },
            "annotate": {
                "type": "boolean",
                "description": "Add numbered labels to screenshot for vision model reference (action='screenshot' only).",
            },
            "template_path": {
                "type": "string",
                "description": "Path to a template image (PNG/JPG) for action='visual_locate'.",
            },
            "confidence": {
                "type": "number",
                "description": "Minimum confidence threshold for visual_locate (0.0-1.0, default 0.8).",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Option values for action='select' (dropdown).",
            },
            "file_path": {
                "type": "string",
                "description": "Local file path for action='upload', target path for action='download', or save path for action='pdf'.",
            },
            "text": {
                "type": "string",
                "description": "Text to wait for on the page (action='wait_for').",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Timeout in milliseconds for wait_for, wait_for_disappear, wait_for_navigation, and wait_for_network_idle (default 10000). For wait_for_navigation, 'milliseconds' is also accepted as a fallback name.",
            },
            "interval_ms": {
                "type": "integer",
                "description": "Polling interval in milliseconds for wait_for (default 500).",
            },
            "duration_ms": {
                "type": "integer",
                "description": "Duration in milliseconds to capture network traffic (action='network_log', default 5000).",
            },
            "filter": {
                "type": "string",
                "description": "Filter string for network_log to only show URLs/requests containing this text.",
            },
            "modify_action": {
                "type": "string",
                "enum": ["inject_headers", "block_urls", "mock_response", "user_agent", "clear"],
                "description": "Modification type for action='network_modify'.",
            },
            "headers": {
                "type": "object",
                "description": "Headers to inject for network_modify inject_headers (key-value pairs).",
            },
            "url_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "URL patterns to block or mock for action='network_modify'.",
            },
            "mock_data": {
                "type": "object",
                "description": "Mock response data for network_modify mock_response: {status, body, headers}.",
            },
            "user_agent": {
                "type": "string",
                "description": "Custom User-Agent string for network_modify user_agent.",
            },
            "storage_action": {
                "type": "string",
                "enum": ["get", "set", "delete", "clear", "list"],
                "description": "Storage operation for action='storage' (default: get).",
            },
            "storage_type": {
                "type": "string",
                "enum": ["localStorage", "sessionStorage", "cookies"],
                "description": "Storage target for action='storage' (default: localStorage).",
            },
            "storage_key": {
                "type": "string",
                "description": "Key for storage get/set/delete.",
            },
            "storage_value": {
                "type": "string",
                "description": "Value for storage set.",
            },
            "console_level": {
                "type": "string",
                "enum": ["all", "error", "warn", "info", "log"],
                "description": "Filter level for action='console' (default: all).",
            },
            "width": {
                "type": "integer",
                "description": "Viewport width in pixels for action='set_viewport'.",
            },
            "height": {
                "type": "integer",
                "description": "Viewport height in pixels for action='set_viewport'.",
            },
            "behavior": {
                "type": "string",
                "enum": ["smooth", "auto", "instant"],
                "description": "Scroll behavior for action='scroll_into_view' (default: smooth).",
            },
            "block": {
                "type": "string",
                "enum": ["start", "center", "end", "nearest"],
                "description": "Vertical alignment for action='scroll_into_view' (default: center).",
            },
            "include_html": {
                "type": "boolean",
                "description": "For action='page_source': include raw HTML in output (default true).",
            },
            "expected_url": {
                "type": "string",
                "description": "For action='wait_for_navigation': substring expected in the URL after navigation.",
            },
            "attribute_name": {
                "type": "string",
                "description": "DOM attribute name for action='get_attribute' or 'set_attribute' (e.g. 'href', 'src', 'data-id').",
            },
            "attribute_value": {
                "type": "string",
                "description": "Value to set for action='set_attribute'.",
            },
            "property_name": {
                "type": "string",
                "description": "JS property name for action='get_property' (e.g. 'value', 'checked', 'innerText', 'innerHTML').",
            },
            "device_name": {
                "type": "string",
                "enum": ["iPhone 14", "iPhone 14 Pro Max", "Pixel 7", "iPad Mini", "reset"],
                "description": "Device preset for action='emulate_device'. Use 'reset' to restore desktop.",
            },
            "idle_duration_ms": {
                "type": "integer",
                "description": "For action='wait_for_network_idle': ms of no network activity before returning (default 2000).",
            },
            "timezone": {
                "type": "string",
                "description": "Timezone ID for action='set_timezone' (e.g. 'Asia/Shanghai', 'America/New_York'). Pass empty string to reset.",
            },
            "locale": {
                "type": "string",
                "description": "Locale for action='set_locale' (e.g. 'zh-CN', 'en-US'). Pass empty string to reset.",
            },
            "latitude": {
                "type": "number",
                "description": "Latitude for action='set_geolocation' (-90 to 90).",
            },
            "longitude": {
                "type": "number",
                "description": "Longitude for action='set_geolocation' (-180 to 180).",
            },
            "accuracy": {
                "type": "number",
                "description": "Accuracy in meters for action='set_geolocation' (default 100).",
            },
        },
        "required": ["action"],
    }


__all__ = ["TOOL_DESCRIPTION", "build_input_schema"]
