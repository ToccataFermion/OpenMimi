"""Solve xft slider CAPTCHA using image template matching + CDP drag.

Usage:
    cd D:\Programs\projects\OpenMimi
    .venv/Scripts/python.exe scripts/test_xft_captcha_solve.py
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


def find_gap_position(background: Image.Image, template: Image.Image) -> tuple[int, int, int]:
    """Find the puzzle-piece gap using edge density around the template perimeter.

    The gap is a cutout in the background with strong edges around its border.
    We search for the position where the template mask perimeter aligns with
    the strongest edges in the background image.
    """
    from PIL import ImageFilter

    bg = background.convert("RGBA")
    tm = template.convert("RGBA")

    bg_w, bg_h = bg.size
    tm_w, tm_h = tm.size

    alpha = tm.split()[3]
    mask_pixels = alpha.load()
    min_x, min_y = tm_w, tm_h
    max_x, max_y = 0, 0
    for y in range(tm_h):
        for x in range(tm_w):
            if mask_pixels[x, y] > 128:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if max_x <= min_x or max_y <= min_y:
        print("No non-transparent region found in template")
        return 0

    mask_w = max_x - min_x + 1
    mask_h = max_y - min_y + 1
    print(f"Template bbox: ({min_x}, {min_y}) to ({max_x}, {max_y}), size {mask_w}x{mask_h}")

    mask_coords = []
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if mask_pixels[x, y] > 128:
                mask_coords.append((x - min_x, y - min_y))

    # Build perimeter: mask pixels that border a non-mask pixel
    mask_set = set(mask_coords)
    perimeter = []
    for dx, dy in mask_coords:
        for ddx, ddy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            if (dx + ddx, dy + ddy) not in mask_set:
                perimeter.append((dx, dy))
                break
    if not perimeter:
        perimeter = mask_coords

    bg_gray = bg.convert("L")
    edges = bg_gray.filter(ImageFilter.FIND_EDGES)
    edge_pixels = edges.load()

    search_start = mask_w
    search_end = bg_w - mask_w + 1

    scores = []
    for x in range(search_start, search_end):
        edge_sum = 0
        for dx, dy in perimeter:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                edge_sum += edge_pixels[px, py]

        # Also compute interior variance as secondary signal
        values = []
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                values.append(bg_gray.getpixel((px, py)))
        mean = sum(values) / len(values) if values else 0
        var = sum((v - mean) ** 2 for v in values) / len(values) if values else 0

        # Combined: high edge density + moderate-low variance (gap is uniform-ish)
        score = edge_sum - var * 0.05
        scores.append((x, score, edge_sum, var))

    scores.sort(key=lambda s: s[1], reverse=True)
    print("Top 15 gap candidates:")
    for i in range(min(15, len(scores))):
        print(f"  x={scores[i][0]:3d}, score={scores[i][1]:.1f}, edges={scores[i][2]}, var={scores[i][3]:.1f}")

    best_x = scores[0][0]
    print(f"Selected gap position: x={best_x}")
    return best_x, min_x, min_y


async def cdp_drag(page, start_x: int, start_y: int, distance: int, steps: int = 35) -> None:
    """Perform a human-like drag using CDP dispatchMouseEvent directly.

    browser_use's Mouse.down() sends mousePressed at (0, 0) instead of the
    current position, so we bypass it and send CDP events directly.
    Uses sine ease-in-out and small y-wiggle for natural movement.
    """
    import math

    client = page._client
    session_id = page._session_id

    await client.send.Input.dispatchMouseEvent(
        {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1, "pointerType": "mouse"},
        session_id=session_id,
    )

    for i in range(1, steps + 1):
        t = i / steps
        # Sine ease in-out
        ease = 0.5 * (1 - math.cos(t * math.pi))
        cx = start_x + int(distance * ease)
        # Small y-wiggle with varying amplitude
        cy = start_y + int(3 * math.sin(i * 0.7))
        await client.send.Input.dispatchMouseEvent(
            {"type": "mouseMoved", "x": cx, "y": cy, "button": "left", "buttons": 1, "pointerType": "mouse"},
            session_id=session_id,
        )
        # Variable delay for human-like timing
        await asyncio.sleep(0.02 + 0.015 * math.sin(i * 0.3))

    end_x = start_x + distance
    await client.send.Input.dispatchMouseEvent(
        {"type": "mouseReleased", "x": end_x, "y": start_y, "button": "left", "clickCount": 1, "pointerType": "mouse"},
        session_id=session_id,
    )


async def measure_drag_ratio(page, sx: int, sy: int) -> float:
    """Do a small test drag and return the piece-movement / drag-distance ratio."""
    before = await page.evaluate("""() => {
        const dragImg = document.querySelector('.dragImage');
        const r = dragImg ? dragImg.getBoundingClientRect() : null;
        return {x: r ? Math.round(r.left) : 0};
    }""")
    if isinstance(before, str):
        before = json.loads(before)

    await cdp_drag(page, sx, sy, 50, steps=10)
    await asyncio.sleep(1.5)

    after = await page.evaluate("""() => {
        const dragImg = document.querySelector('.dragImage');
        const r = dragImg ? dragImg.getBoundingClientRect() : null;
        return {x: r ? Math.round(r.left) : 0};
    }""")
    if isinstance(after, str):
        after = json.loads(after)

    moved = after.get("x", 0) - before.get("x", 0)
    ratio = moved / 50 if 50 > 0 else 0
    print(f"Test drag: piece moved {moved}px for 50px drag, ratio={ratio:.4f}")
    return ratio


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

        if result.details and result.details.get("tab_count", 1) > 1:
            tabs = result.details.get("open_tabs", [])
            popup_idx = None
            for i, t in enumerate(tabs, start=1):
                if "#/index" in (t.get("url") or ""):
                    popup_idx = i
            if popup_idx is not None:
                print(f"Switching to popup tab {popup_idx}")
                result = await tool({"action": "switch_tab", "tab_index": popup_idx})
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
        await asyncio.sleep(2.0)

        # Check for consent dialog
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

        # CAPTCHA phase
        print("\n" + "=" * 60)
        print("CAPTCHA phase: extract images and solve")
        print("=" * 60)

        page = await tool._maybe_get_page()
        if not page:
            print("No page available")
            return

        # Get CAPTCHA meta
        captcha_meta = await page.evaluate("""() => {
            const result = {
                hasBottomImage: false,
                hasDragImage: false,
                sliderHandle: null,
                captchaRect: null,
                bottomImageRect: null,
                dragImageRect: null,
                dragButtonRect: null
            };
            const captcha = document.querySelector('.xftImageVerify');
            if (captcha) {
                const r = captcha.getBoundingClientRect();
                result.captchaRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
            }
            const bottomImg = document.querySelector('.bottomImage');
            if (bottomImg) {
                result.hasBottomImage = true;
                const r = bottomImg.getBoundingClientRect();
                result.bottomImageRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
            }
            const dragImg = document.querySelector('.dragImage');
            if (dragImg) {
                result.hasDragImage = true;
                const r = dragImg.getBoundingClientRect();
                result.dragImageRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
            }
            const dragBtn = document.querySelector('.imageVerifyDragButton');
            if (dragBtn) {
                const r = dragBtn.getBoundingClientRect();
                result.dragButtonRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
                result.sliderHandle = {
                    x: Math.round(r.left + r.width/2),
                    y: Math.round(r.top + r.height/2),
                    w: Math.round(r.width),
                    h: Math.round(r.height)
                };
            }
            return JSON.stringify(result);
        }""")
        if isinstance(captcha_meta, str):
            try:
                captcha_meta = json.loads(captcha_meta)
            except json.JSONDecodeError:
                captcha_meta = {}

        print(f"CAPTCHA meta: {json.dumps(captcha_meta, ensure_ascii=False, indent=2)}")

        slider_handle = captcha_meta.get("sliderHandle")
        if not slider_handle:
            print("Missing slider handle")
            screenshot_b64 = await page.screenshot(format="png")
            with open("xft_captcha_fail.png", "wb") as f:
                f.write(base64.b64decode(screenshot_b64))
            print("Saved fail screenshot to xft_captcha_fail.png")
            return

        # Extract image data
        bottom_src = await page.evaluate("""() => { const img = document.querySelector('.bottomImage'); return img ? img.src : null; }""")
        drag_src = await page.evaluate("""() => { const img = document.querySelector('.dragImage'); return img ? img.src : null; }""")

        if not bottom_src or not drag_src:
            print("Missing CAPTCHA images")
            return

        def data_url_to_image(src: str) -> Image.Image:
            prefix, b64 = src.split(",", 1)
            data = base64.b64decode(b64)
            return Image.open(io.BytesIO(data))

        bottom_img = data_url_to_image(bottom_src)
        drag_img = data_url_to_image(drag_src)

        print(f"Bottom image: {bottom_img.size}, mode={bottom_img.mode}")
        print(f"Drag image: {drag_img.size}, mode={drag_img.mode}")

        bottom_img.save("xft_bottom_image.png")
        drag_img.save("xft_drag_image.png")
        print("Saved images")

        gap_x_in_bg, piece_min_x, piece_min_y = find_gap_position(bottom_img, drag_img)
        print(f"Gap detection found gap at background x={gap_x_in_bg}, piece offset=({piece_min_x},{piece_min_y})")

        # Get slider handle position
        sx, sy = slider_handle["x"], slider_handle["y"]
        print(f"Slider handle at ({sx}, {sy})")

        # Use cached ratio from previous measurements (~0.94).
        # A separate test drag can confuse the CAPTCHA (it expects one continuous gesture).
        RATIO = 0.94

        # Compute required drag distance.
        # The piece starts at background x=piece_min_x (internal offset within dragImage).
        # It needs to reach background x=gap_x_in_bg.
        # Total piece movement needed = gap_x_in_bg - piece_min_x.
        # Drag distance = piece_movement / ratio.
        total_piece_movement = gap_x_in_bg - piece_min_x
        total_drag = round(total_piece_movement / RATIO)
        print(f"\nTotal drag needed: {total_drag}px (gap={gap_x_in_bg}, piece_offset={piece_min_x}, ratio={RATIO})")

        # Perform single continuous drag from the initial handle position.
        print(f"\nPerforming single drag of {total_drag}px via CDP...")
        await cdp_drag(page, sx, sy, total_drag, steps=40)
        print("Drag complete")
        await asyncio.sleep(3.0)

        # Check result via DOM state and text extraction
        dom_state = await page.evaluate("""() => {
            const captcha = document.querySelector('.xftImageVerify');
            const dragBtn = document.querySelector('.imageVerifyDragButton');
            const dragImg = document.querySelector('.dragImage');
            const textEl = document.querySelector('.imageVerifyText');
            const refreshBtn = document.querySelector('.imageRefresh');

            // Check for success indicators
            const successText = document.body.innerText.includes('验证成功');
            const failText = document.body.innerText.includes('验证失败');
            const loginSuccess = document.body.innerText.includes('登录成功');

            return JSON.stringify({
                captchaVisible: captcha ? captcha.style.display !== 'none' : false,
                btnLeft: dragBtn ? dragBtn.style.left : null,
                imgLeft: dragImg ? dragImg.style.left : null,
                verifyText: textEl ? textEl.innerText : null,
                hasRefresh: !!refreshBtn,
                successText,
                failText,
                loginSuccess,
                bodyText: document.body.innerText.slice(0, 300)
            });
        }""")
        if isinstance(dom_state, str):
            dom_state = json.loads(dom_state)

        print(f"\nDOM state: {json.dumps(dom_state, ensure_ascii=False, indent=2)}")

        result = await tool({"action": "extract", "instruction": "get text"})
        text = result.output
        print(f"\nPage text after drag: {text[:600]}")
        with open("xft_captcha_result.txt", "w", encoding="utf-8") as f:
            f.write(text)

        if dom_state.get("successText") or dom_state.get("loginSuccess") or "验证成功" in text or "登录成功" in text:
            print("[RESULT] CAPTCHA/Login success!")
        elif dom_state.get("failText") or "验证失败" in text:
            print("[RESULT] CAPTCHA validation failed")
        elif "系统异常" in text:
            print("[RESULT] Server error")
        elif "密码错误" in text:
            print("[RESULT] Wrong password")
        elif "失败" in text or "error" in text.lower():
            print("[RESULT] Some failure")
        else:
            print("[RESULT] Unknown result - check screenshot")

        screenshot_b64 = await page.screenshot(format="png")
        with open("xft_after_drag.png", "wb") as f:
            f.write(base64.b64decode(screenshot_b64))
        print("Saved after-drag screenshot to xft_after_drag.png")

        print("\nKeeping browser open for 10s...")
        await asyncio.sleep(10.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
