"""Measure CAPTCHA drag ratio by performing a test drag and reading piece position."""
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


def find_gap_position(background: Image.Image, template: Image.Image) -> int:
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
        return 0

    mask_w = max_x - min_x + 1
    mask_coords = []
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if mask_pixels[x, y] > 128:
                mask_coords.append((x - min_x, y - min_y))

    bg_gray = bg.convert("L")
    search_start = mask_w
    search_end = bg_w - mask_w + 1

    WHITE_THRESHOLD = 200
    scores = []
    for x in range(search_start, search_end):
        white_count = 0
        total_brightness = 0
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                b = bg_gray.getpixel((px, py))
                total_brightness += b
                if b > WHITE_THRESHOLD:
                    white_count += 1
        avg_bright = total_brightness / len(mask_coords)
        white_ratio = white_count / len(mask_coords)
        score = white_ratio * 1000 + avg_bright
        scores.append((x, score, avg_bright, white_ratio))

    scores.sort(key=lambda s: s[1], reverse=True)
    print("Top 10 candidates:")
    for i in range(min(10, len(scores))):
        print(f"  x={scores[i][0]:3d}, bright={scores[i][2]:.1f}, white={scores[i][3]:.2%}")
    return scores[0][0]


async def cdp_drag(page, start_x: int, start_y: int, distance: int, steps: int = 20) -> None:
    """Perform a drag using CDP dispatchMouseEvent with proper coordinates.

    browser_use's Mouse.down() sends mousePressed at (0, 0) instead of the
    current position, so we bypass it and send CDP events directly.
    We also set buttons=1 on mouseMoved so the page knows the left button
    is held during the drag.
    """
    client = page._client
    session_id = page._session_id

    # mousePressed at start position
    await client.send.Input.dispatchMouseEvent(
        {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1, "pointerType": "mouse"},
        session_id=session_id,
    )

    for i in range(1, steps + 1):
        t = i / steps
        ease = 1 - (1 - t) ** 3
        cx = start_x + int(distance * ease)
        cy = start_y + (i % 3 - 1)
        await client.send.Input.dispatchMouseEvent(
            {"type": "mouseMoved", "x": cx, "y": cy, "button": "left", "buttons": 1, "pointerType": "mouse"},
            session_id=session_id,
        )
        await asyncio.sleep(0.03)

    # mouseReleased at final position
    end_x = start_x + distance
    await client.send.Input.dispatchMouseEvent(
        {"type": "mouseReleased", "x": end_x, "y": start_y, "button": "left", "clickCount": 1, "pointerType": "mouse"},
        session_id=session_id,
    )


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_captcha_")
    tool = BrowserTool(download_dir=download_dir, headless=False)

    try:
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(1.0)

        result = await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(3.0)

        if result.details and result.details.get("tab_count", 1) > 1:
            tabs = result.details.get("open_tabs", [])
            popup_idx = None
            for i, t in enumerate(tabs, start=1):
                if "#/index" in (t.get("url") or ""):
                    popup_idx = i
            if popup_idx is not None:
                await tool({"action": "switch_tab", "tab_index": popup_idx})
                await asyncio.sleep(1.0)

        await tool({"action": "click", "target_text": "密码登录"})
        await asyncio.sleep(1.5)

        await tool({"action": "click", "target_text": "我已阅读并同意"})
        await asyncio.sleep(1.0)

        page = await tool._maybe_get_page()
        if page:
            await page.evaluate("""() => {
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
            }""")
        await asyncio.sleep(1.0)

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
            await tool({"action": "click", "coordinate": [login_btn["x"], login_btn["y"]]})
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
                await tool({"action": "click", "coordinate": [dialog_info["x"], dialog_info["y"]]})
                await asyncio.sleep(2.0)

        # CAPTCHA phase
        page = await tool._maybe_get_page()
        if not page:
            print("No page")
            return

        # Get meta
        captcha_meta = await page.evaluate("""() => {
            const result = {hasBottomImage: false, hasDragImage: false, sliderHandle: null};
            const bottomImg = document.querySelector('.bottomImage');
            if (bottomImg) result.hasBottomImage = true;
            const dragImg = document.querySelector('.dragImage');
            if (dragImg) {
                result.hasDragImage = true;
                const r = dragImg.getBoundingClientRect();
                result.dragImageRect = {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
                result.dragImageLeft = dragImg.style.left;
                result.dragImageTransform = window.getComputedStyle(dragImg).transform;
            }
            const dragContainer = document.querySelector('.imageVerifyDrag');
            if (dragContainer) {
                const children = Array.from(dragContainer.querySelectorAll('*'));
                for (const child of children) {
                    const r = child.getBoundingClientRect();
                    if (r.width > 10 && r.height > 10 && r.width < 80 && r.height < 80) {
                        result.sliderHandle = {
                            x: Math.round(r.left + r.width/2),
                            y: Math.round(r.top + r.height/2),
                            w: Math.round(r.width),
                            h: Math.round(r.height)
                        };
                        break;
                    }
                }
            }
            return JSON.stringify(result);
        }""")
        if isinstance(captcha_meta, str):
            captcha_meta = json.loads(captcha_meta)
        print(f"CAPTCHA meta: {json.dumps(captcha_meta, indent=2)}")

        slider_handle = captcha_meta.get("sliderHandle")
        if not slider_handle:
            print("No slider handle")
            return

        bottom_src = await page.evaluate("""() => {
            const img = document.querySelector('.bottomImage');
            return img ? img.src : null;
        }""")
        drag_src = await page.evaluate("""() => {
            const img = document.querySelector('.dragImage');
            return img ? img.src : null;
        }""")

        if not bottom_src or not drag_src:
            print("Missing images")
            return

        def data_url_to_image(src: str) -> Image.Image:
            prefix, b64 = src.split(",", 1)
            data = base64.b64decode(b64)
            return Image.open(io.BytesIO(data))

        bottom_img = data_url_to_image(bottom_src)
        drag_img = data_url_to_image(drag_src)
        bottom_img.save("xft_bottom_image.png")
        drag_img.save("xft_drag_image.png")

        gap_x = find_gap_position(bottom_img, drag_img)
        print(f"Gap at background x={gap_x}")

        sx, sy = slider_handle["x"], slider_handle["y"]
        print(f"Slider handle at ({sx}, {sy})")

        # Measure piece position before drag
        before_info = await page.evaluate("""() => {
            const dragImg = document.querySelector('.dragImage');
            const btn = document.querySelector('.imageVerifyDragButton');
            const progress = document.querySelector('.imageVerifyDragProgressbar');
            const dragR = dragImg ? dragImg.getBoundingClientRect() : null;
            const btnR = btn ? btn.getBoundingClientRect() : null;
            return {
                dragLeft: dragImg ? dragImg.style.left : null,
                dragTransform: dragImg ? window.getComputedStyle(dragImg).transform : null,
                dragRect: dragR ? {x: Math.round(dragR.left), y: Math.round(dragR.top)} : null,
                btnLeft: btn ? btn.style.left : null,
                btnTransform: btn ? window.getComputedStyle(btn).transform : null,
                btnRect: btnR ? {x: Math.round(btnR.left), y: Math.round(btnR.top), w: Math.round(btnR.width), h: Math.round(btnR.height)} : null,
                progressWidth: progress ? progress.style.width : null
            };
        }""")
        if isinstance(before_info, str):
            before_info = json.loads(before_info)
        print(f"Before drag: {json.dumps(before_info, indent=2)}")

        # Perform a test drag of 100px using CDP directly
        TEST_DRAG = 100
        print(f"\nTest drag of {TEST_DRAG}px via CDP...")
        await cdp_drag(page, sx, sy, TEST_DRAG, steps=20)
        await asyncio.sleep(2.0)

        # Measure piece position after test drag
        after_info = await page.evaluate("""() => {
            const dragImg = document.querySelector('.dragImage');
            const btn = document.querySelector('.imageVerifyDragButton');
            const progress = document.querySelector('.imageVerifyDragProgressbar');
            const dragR = dragImg ? dragImg.getBoundingClientRect() : null;
            const btnR = btn ? btn.getBoundingClientRect() : null;
            return {
                dragLeft: dragImg ? dragImg.style.left : null,
                dragTransform: dragImg ? window.getComputedStyle(dragImg).transform : null,
                dragRect: dragR ? {x: Math.round(dragR.left), y: Math.round(dragR.top)} : null,
                btnLeft: btn ? btn.style.left : null,
                btnTransform: btn ? window.getComputedStyle(btn).transform : null,
                btnRect: btnR ? {x: Math.round(btnR.left), y: Math.round(btnR.top), w: Math.round(btnR.width), h: Math.round(btnR.height)} : null,
                progressWidth: progress ? progress.style.width : null
            };
        }""")
        if isinstance(after_info, str):
            after_info = json.loads(after_info)
        print(f"After test drag: {json.dumps(after_info, indent=2)}")

        # Calculate ratio from bounding rect changes
        before_drag_x = before_info.get('dragRect', {}).get('x', 0) if before_info.get('dragRect') else 0
        after_drag_x = after_info.get('dragRect', {}).get('x', 0) if after_info.get('dragRect') else 0
        moved = after_drag_x - before_drag_x
        ratio = moved / TEST_DRAG if TEST_DRAG > 0 else 0
        print(f"Piece rect moved from {before_drag_x} to {after_drag_x} (delta={moved}), ratio={ratio:.3f}")

        # Also check button rect
        before_btn_x = before_info.get('btnRect', {}).get('x', 0) if before_info.get('btnRect') else 0
        after_btn_x = after_info.get('btnRect', {}).get('x', 0) if after_info.get('btnRect') else 0
        btn_moved = after_btn_x - before_btn_x
        btn_ratio = btn_moved / TEST_DRAG if TEST_DRAG > 0 else 0
        print(f"Button rect moved from {before_btn_x} to {after_btn_x} (delta={btn_moved}), ratio={btn_ratio:.3f}")

        # Now compute actual drag distance needed
        effective_ratio = ratio if ratio > 0 else btn_ratio
        if effective_ratio > 0:
            actual_drag = int(gap_x / effective_ratio)
            print(f"Estimated actual drag needed: {actual_drag}px")

            # The piece needs to move to gap_x. Current piece position is after_drag_x (relative to viewport).
            # The piece starts at viewport x=470 (from dragImageRect). So current piece offset is after_drag_x - 470.
            drag_image_start_x = captcha_meta.get("dragImageRect", {}).get("x", 470)
            current_piece_offset = after_drag_x - drag_image_start_x
            remaining = gap_x - current_piece_offset
            remaining_drag = int(remaining / effective_ratio)
            print(f"Current piece offset: {current_piece_offset}, target: {gap_x}, remaining drag: {remaining_drag}")

            if remaining_drag > 10:
                print(f"Performing remaining drag of {remaining_drag}px...")
                # Use current slider handle position (btn center)
                hx = after_btn_x + (after_info.get('btnRect', {}).get('w', 60) // 2)
                hy = after_info.get('btnRect', {}).get('y', sy - 20) + (after_info.get('btnRect', {}).get('h', 40) // 2)
                await cdp_drag(page, hx, hy, remaining_drag, steps=20)
                print("Remaining drag complete")
                await asyncio.sleep(2.0)

        # Check result
        result = await tool({"action": "extract", "instruction": "get text"})
        text = result.output
        print(f"Page text: {text[:500]}")

        screenshot_b64 = await page.screenshot(format="png")
        with open("xft_after_drag.png", "wb") as f:
            f.write(base64.b64decode(screenshot_b64))
        print("Saved screenshot to xft_after_drag.png")

        print("\nKeeping browser open for 10s...")
        await asyncio.sleep(10.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
