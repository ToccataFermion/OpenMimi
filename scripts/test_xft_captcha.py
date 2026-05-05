"""Focused script to analyze and solve the xft slider CAPTCHA.

Usage:
    cd D:\Programs\projects\OpenMimi
    python scripts\test_xft_captcha.py
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.browser import BrowserTool


# JS to deeply inspect CAPTCHA-related elements
_INSPECT_CAPTCHA_JS = """() => {
    const result = {
        captchaContainers: [],
        sliderElements: [],
        puzzlePieces: [],
        images: [],
        globalVars: [],
        cssVars: []
    };

    // Find containers with captcha/slider/verify related classes or IDs
    document.querySelectorAll('*').forEach(el => {
        const cls = (el.className || '').toString().toLowerCase();
        const id = (el.id || '').toLowerCase();
        const tag = el.tagName.toLowerCase();
        if (cls.includes('captcha') || cls.includes('slider') || cls.includes('verify') || cls.includes('拼图') || cls.includes('滑块') ||
            id.includes('captcha') || id.includes('slider') || id.includes('verify')) {
            const r = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            result.captchaContainers.push({
                tag: el.tagName,
                class: el.className,
                id: el.id,
                rect: {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)},
                style: {
                    left: style.left,
                    top: style.top,
                    transform: style.transform,
                    position: style.position,
                    backgroundImage: style.backgroundImage.slice(0, 100),
                    width: style.width,
                    height: style.height
                },
                dataAttrs: Object.fromEntries(Array.from(el.attributes).filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value]))
            });
        }
    });

    // Find all img elements
    document.querySelectorAll('img').forEach(img => {
        const r = img.getBoundingClientRect();
        if (r.width > 50 && r.height > 50) {
            result.images.push({
                src: img.src.slice(0, 200),
                rect: {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)},
                class: img.className,
                id: img.id,
                style: {
                    transform: window.getComputedStyle(img).transform,
                    left: window.getComputedStyle(img).left
                }
            });
        }
    });

    // Find divs with background images
    document.querySelectorAll('div').forEach(div => {
        const style = window.getComputedStyle(div);
        const bg = style.backgroundImage;
        const r = div.getBoundingClientRect();
        if (bg && bg !== 'none' && r.width > 50 && r.height > 50) {
            result.images.push({
                isBg: true,
                src: bg.slice(0, 200),
                rect: {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)},
                class: div.className,
                id: div.id,
                style: {
                    transform: style.transform,
                    left: style.left,
                    top: style.top
                }
            });
        }
    });

    // Search for global variables related to captcha
    for (const key of Object.keys(window)) {
        const val = window[key];
        if (typeof val === 'object' && val !== null) {
            try {
                const keys = Object.keys(val);
                if (keys.some(k => k.toLowerCase().includes('slider') || k.toLowerCase().includes('captcha') || k.toLowerCase().includes('verify') || k.toLowerCase().includes('gap'))) {
                    result.globalVars.push({
                        name: key,
                        keys: keys.filter(k => k.toLowerCase().includes('slider') || k.toLowerCase().includes('captcha') || k.toLowerCase().includes('verify') || k.toLowerCase().includes('gap') || k.toLowerCase().includes('x') || k.toLowerCase().includes('y') || k.toLowerCase().includes('width') || k.toLowerCase().includes('offset'))
                    });
                }
            } catch (e) {}
        }
    }

    return JSON.stringify(result);
}
"""


# JS to try to get the exact gap/target position from common CAPTCHA implementations
_EXTRACT_TARGET_JS = """() => {
    // Try common patterns for slider CAPTCHA target positions
    const result = { methods: [] };

    // Method 1: Look for specific global objects
    const candidates = ['captcha', 'slider', 'verify', 'geetest', 'nc', 'noCaptcha'];
    for (const name of candidates) {
        if (window[name]) {
            try {
                const keys = Object.keys(window[name]);
                result.methods.push({source: 'window.' + name, keys: keys});
            } catch (e) {}
        }
    }

    // Method 2: Check for elements with transform that might be the puzzle piece
    const pieces = Array.from(document.querySelectorAll('div, img')).filter(el => {
        const t = window.getComputedStyle(el).transform;
        return t && t !== 'none' && t.includes('matrix');
    }).map(el => {
        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        // Parse matrix to get translateX
        const m = new DOMMatrix(style.transform);
        return {
            tag: el.tagName,
            class: el.className.slice(0, 50),
            translateX: m.m41,
            rect: {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)}
        };
    });
    result.methods.push({source: 'transform_elements', elements: pieces});

    // Method 3: Find the slider button and track
    const sliders = Array.from(document.querySelectorAll('*')).filter(el => {
        const text = (el.innerText || '').trim();
        return text.includes('>>') || text.includes('→') || text.includes('滑动');
    }).map(el => {
        const r = el.getBoundingClientRect();
        return {tag: el.tagName, class: el.className.slice(0, 50), text: (el.innerText || '').trim().slice(0, 20), rect: {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)}};
    });
    result.methods.push({source: 'slider_buttons', elements: sliders});

    return JSON.stringify(result);
}
"""


def find_gap_in_captcha(screenshot_bytes: bytes) -> tuple[int, int] | None:
    """Analyze screenshot to find the slider handle and gap positions.

    Returns (handle_x, gap_x) in viewport coordinates, or None if detection fails.
    """
    img = Image.open(io.BytesIO(screenshot_bytes))
    w, h = img.size
    print(f"Screenshot size: {w}x{h}")

    # Convert to RGB for analysis
    rgb = img.convert("RGB")

    # The CAPTCHA modal is typically centered.
    # Based on the screenshot, the CAPTCHA image seems to be in the center-upper area.
    # Let's search for the blue slider button (common color ~ #1677ff or similar)
    handle_y = None
    handle_x = None

    # Search bottom half for blue slider button
    for y in range(h // 2, h - 10):
        for x in range(10, w - 10):
            r, g, b = rgb.getpixel((x, y))
            # Blue slider button - high blue, moderate green, low red
            if b > 180 and g > 100 and r < 100 and b > r + 80:
                # Check if this is part of a button-sized region
                count = 0
                for dx in range(-5, 6):
                    for dy in range(-5, 6):
                        if 0 <= x + dx < w and 0 <= y + dy < h:
                            pr, pg, pb = rgb.getpixel((x + dx, y + dy))
                            if pb > 150 and pb > pr + 50:
                                count += 1
                if count > 50:
                    handle_x = x
                    handle_y = y
                    break
        if handle_x:
            break

    if handle_x:
        print(f"Found blue slider at approx ({handle_x}, {handle_y})")
    else:
        print("Could not find blue slider button by color")
        # Fallback: assume it's at a common position
        handle_x = w // 2 - 100
        handle_y = h // 2 + 150

    # Now find the gap.
    # Strategy: look for the puzzle piece shape in the upper portion of the CAPTCHA.
    # The gap often has a white/light border or the missing piece creates a recognizable pattern.
    # Let's crop the area where the CAPTCHA image is likely to be.

    # Estimate CAPTCHA image region (centered, upper-middle)
    captcha_left = max(0, w // 2 - 200)
    captcha_right = min(w, w // 2 + 200)
    captcha_top = max(0, h // 2 - 150)
    captcha_bottom = min(h, h // 2 + 100)

    print(f"Searching CAPTCHA image region: x={captcha_left}-{captcha_right}, y={captcha_top}-{captcha_bottom}")

    # Save cropped region for debugging
    crop = rgb.crop((captcha_left, captcha_top, captcha_right, captcha_bottom))
    crop.save("xft_captcha_crop.png")
    print("Saved cropped CAPTCHA region to xft_captcha_crop.png")

    # Gap detection heuristic:
    # In many jigsaw CAPTCHAs, the gap has a slightly different appearance.
    # One common pattern: the missing piece area has a thin white/light border.
    # Another: the puzzle piece on the left is semi-transparent.
    # Let's look for vertical edges that might indicate the gap position.

    cw, ch = crop.size

    # Compute average brightness per column in the cropped region
    col_brightness = []
    for x in range(cw):
        total = 0
        for y in range(ch):
            r, g, b = crop.getpixel((x, y))
            total += (r + g + b) / 3
        col_brightness.append(total / ch)

    # Find significant brightness anomalies - the gap border might be brighter
    # Look for local maxima in brightness variance
    gap_x_in_crop = None
    best_score = 0

    for x in range(10, cw - 10):
        # Measure edge strength at this column
        left_avg = sum(col_brightness[x - 5:x]) / 5
        right_avg = sum(col_brightness[x:x + 5]) / 5
        diff = abs(left_avg - right_avg)

        # Also check for a "hole" pattern: the gap area might be slightly darker or have a border
        # Compute variance in a window
        window = [col_brightness[x + dx] for dx in range(-5, 6)]
        variance = sum((v - sum(window) / len(window)) ** 2 for v in window) / len(window)

        score = diff + variance * 0.5
        if score > best_score:
            best_score = score
            gap_x_in_crop = x

    if gap_x_in_crop:
        gap_x_viewport = captcha_left + gap_x_in_crop
        print(f"Estimated gap at crop x={gap_x_in_crop}, viewport x={gap_x_viewport}")
        return (handle_x, gap_x_viewport)

    return None


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_captcha_")
    tool = BrowserTool(download_dir=download_dir, headless=False)

    try:
        print("=" * 60)
        print("Navigate to xft.cmbchina.com")
        print("=" * 60)
        result = await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        print(f"Result: {result.output}")
        await asyncio.sleep(1.0)

        print("\nClick '登录'")
        result = await tool({"action": "click", "target_text": "登录"})
        print(f"Result: {result.output}")
        await asyncio.sleep(3.0)

        # Switch to popup tab if needed
        if result.details and result.details.get("tab_count", 1) > 1:
            tabs = result.details.get("open_tabs", [])
            popup_idx = None
            for i, t in enumerate(tabs, start=1):
                if "#/index" in (t.get("url") or ""):
                    popup_idx = i
            if popup_idx is not None:
                print(f"Switching to popup tab {popup_idx}")
                result = await tool({"action": "switch_tab", "tab_index": popup_idx})
                print(f"Switch result: {result.output}")
                await asyncio.sleep(1.0)

        print("\nClick '密码登录' tab")
        result = await tool({"action": "click", "target_text": "密码登录"})
        print(f"Result: {result.output}")
        await asyncio.sleep(1.5)

        print("\nClick consent checkbox")
        result = await tool({"action": "click", "target_text": "我已阅读并同意"})
        print(f"Result: {result.output}")
        await asyncio.sleep(1.0)

        print("\nFill phone and password via native setter")
        page = await tool._maybe_get_page()
        if page:
            fill_result = await page.evaluate("""() => {
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                function setNativeValue(el, val) {
                    if (!el) return;
                    nativeSetter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                const inputs = Array.from(document.querySelectorAll('input')).filter(i => {
                    const r = i.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                const phone = inputs.find(i => i.type === 'text');
                const pass = inputs.find(i => i.type === 'password');
                const cb = inputs.find(i => i.type === 'checkbox');
                setNativeValue(phone, '18584828398');
                setNativeValue(pass, 'Liszt123');
                if (cb) { cb.checked = true; cb.dispatchEvent(new Event('change', {bubbles: true})); }
                return {phone: phone ? phone.value : null, pass: pass ? pass.value : null};
            }""")
            print(f"Fill result: {fill_result}")
        await asyncio.sleep(1.0)

        print("\nClick login button (via class selector)")
        page = await tool._maybe_get_page()
        login_btn = None
        if page:
            login_btn = await page.evaluate("""() => {
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                if (!btn) return null;
                const r = btn.getBoundingClientRect();
                return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
            }""")
            if isinstance(login_btn, str):
                try:
                    login_btn = json.loads(login_btn)
                except json.JSONDecodeError:
                    login_btn = None
        if login_btn:
            result = await tool({"action": "click", "coordinate": [login_btn["x"], login_btn["y"]]})
            print(f"Click result: {result.output}")
        else:
            print("Login button not found by class, falling back to text")
            result = await tool({"action": "click", "target_text": "登录"})
            print(f"Click result: {result.output}")
        await asyncio.sleep(2.0)

        # Check for consent dialog and click 同意
        page = await tool._maybe_get_page()
        if page:
            dialog_info = await page.evaluate("""() => {
                const allEls = Array.from(document.querySelectorAll('body *'));
                const agreeBtn = allEls.find(b => {
                    const text = (b.innerText || b.textContent || '').trim();
                    return text === '同意' && b.offsetParent !== null;
                });
                if (agreeBtn) {
                    const r = agreeBtn.getBoundingClientRect();
                    return {found: true, text: '同意', x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
                }
                return {found: false};
            }""")
            print(f"Dialog check: {dialog_info}")
            if isinstance(dialog_info, str):
                try:
                    dialog_info = json.loads(dialog_info)
                except json.JSONDecodeError:
                    dialog_info = {"found": False}
            if dialog_info and dialog_info.get("found"):
                print(f"Clicking 同意 at ({dialog_info['x']}, {dialog_info['y']})")
                result = await tool({"action": "click", "coordinate": [dialog_info["x"], dialog_info["y"]]})
                print(f"Dialog click: {result.output}")
                await asyncio.sleep(2.0)

        # Now CAPTCHA should be visible. Let's inspect and solve it.
        print("\n" + "=" * 60)
        print("CAPTCHA phase: inspect and solve")
        print("=" * 60)

        page = await tool._maybe_get_page()
        if not page:
            print("No page available")
            return

        # Deep inspect
        inspect_raw = await page.evaluate(_INSPECT_CAPTCHA_JS) or "{}"
        try:
            inspect = json.loads(inspect_raw) if isinstance(inspect_raw, str) else inspect_raw
        except Exception:
            inspect = {}
        print(f"Captcha containers: {json.dumps(inspect.get('captchaContainers', []), ensure_ascii=False, indent=2)[:2000]}")
        print(f"Images: {json.dumps(inspect.get('images', []), ensure_ascii=False, indent=2)[:2000]}")

        # Try target extraction
        target_raw = await page.evaluate(_EXTRACT_TARGET_JS) or "{}"
        try:
            target_info = json.loads(target_raw) if isinstance(target_raw, str) else target_raw
        except Exception:
            target_info = {}
        print(f"Target extraction: {json.dumps(target_info, ensure_ascii=False, indent=2)[:2000]}")

        # Take screenshot for image analysis
        screenshot_b64 = await page.screenshot(format="png")
        screenshot_bytes = base64.b64decode(screenshot_b64)
        with open("xft_captcha_screenshot.png", "wb") as f:
            f.write(screenshot_bytes)
        print("Saved screenshot to xft_captcha_screenshot.png")

        # Get slider position via JS first
        slider_pos = await page.evaluate("""() => {
            const allEls = Array.from(document.querySelectorAll('body *'));
            const slider = allEls.find(b => {
                const text = (b.innerText || b.textContent || '').trim();
                return text.includes('>>') || text.includes('→');
            });
            if (slider) {
                const r = slider.getBoundingClientRect();
                return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2), w: Math.round(r.width), h: Math.round(r.height)};
            }
            return null;
        }""")
        print(f"Slider position from JS: {slider_pos}")
        if isinstance(slider_pos, str):
            try:
                slider_pos = json.loads(slider_pos)
            except json.JSONDecodeError:
                slider_pos = None

        # Try image-based gap detection
        gap_result = find_gap_in_captcha(screenshot_bytes)
        if gap_result and slider_pos:
            handle_x, gap_x = gap_result
            sx, sy = slider_pos["x"], slider_pos["y"]
            drag_distance = gap_x - sx
            print(f"Image analysis suggests drag from ({sx}, {sy}) to gap x={gap_x}, distance={drag_distance}")

            # Perform the drag
            if drag_distance > 50:
                print(f"Performing drag of {drag_distance}px...")
                # Use CDP Input.dispatchMouseEvent for drag
                cdp_session = await page._page.context.new_cdp_session(page._page)
                steps = 25
                await cdp_session.send("Input.dispatchMouseEvent", {
                    "type": "mousePressed",
                    "x": sx,
                    "y": sy,
                    "button": "left",
                    "clickCount": 1
                })
                for i in range(1, steps + 1):
                    t = i / steps
                    # Ease out cubic
                    ease = 1 - (1 - t) ** 3
                    cx = sx + int(drag_distance * ease)
                    # Add small random jitter
                    jitter_y = sy + (i % 3 - 1)
                    await cdp_session.send("Input.dispatchMouseEvent", {
                        "type": "mouseMoved",
                        "x": cx,
                        "y": jitter_y,
                        "button": "left"
                    })
                    await asyncio.sleep(0.03 + (i % 3) * 0.01)
                await cdp_session.send("Input.dispatchMouseEvent", {
                    "type": "mouseReleased",
                    "x": sx + drag_distance,
                    "y": sy,
                    "button": "left",
                    "clickCount": 1
                })
                print("Drag complete")
                await asyncio.sleep(3.0)

                # Check result
                result = await tool({"action": "extract", "instruction": "get text"})
                text = result.output
                print(f"Page text after drag: {text[:500]}")
                with open("xft_captcha_result.txt", "w", encoding="utf-8") as f:
                    f.write(text)
        else:
            print("Image-based gap detection failed or slider not found")

        print("\nKeeping browser open for 15s...")
        await asyncio.sleep(15.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
