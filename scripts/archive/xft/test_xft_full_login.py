"""Full xft.cmbchina.com login: CAPTCHA solve + SMS verification flow.

Usage:
    cd D:\Programs\projects\OpenMimi
    .venv/Scripts/python.exe scripts/test_xft_full_login.py
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import tempfile

from PIL import Image, ImageFilter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.browser import BrowserTool


def find_gap_position(background: Image.Image, template: Image.Image) -> tuple[int, int, int]:
    """Find the puzzle-piece gap using edge density around the template perimeter."""
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
        return 0, 0, 0

    mask_w = max_x - min_x + 1
    mask_h = max_y - min_y + 1
    mask_coords = []
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if mask_pixels[x, y] > 128:
                mask_coords.append((x - min_x, y - min_y))

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
        values = []
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                values.append(bg_gray.getpixel((px, py)))
        mean = sum(values) / len(values) if values else 0
        var = sum((v - mean) ** 2 for v in values) / len(values) if values else 0
        score = edge_sum - var * 0.05
        scores.append((x, score, edge_sum, var))

    scores.sort(key=lambda s: s[1], reverse=True)
    print("Top 10 gap candidates:")
    for i in range(min(10, len(scores))):
        print(f"  x={scores[i][0]:3d}, score={scores[i][1]:.1f}, edges={scores[i][2]}, var={scores[i][3]:.1f}")

    best_x = scores[0][0]
    print(f"Selected gap position: x={best_x}")
    return best_x, min_x, min_y


async def cdp_drag(page, start_x: int, start_y: int, distance: int, steps: int = 40) -> None:
    """Human-like drag via CDP dispatchMouseEvent."""
    import math
    client = page._client
    session_id = page._session_id

    await client.send.Input.dispatchMouseEvent(
        {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1, "pointerType": "mouse"},
        session_id=session_id,
    )

    for i in range(1, steps + 1):
        t = i / steps
        ease = 0.5 * (1 - math.cos(t * math.pi))
        cx = start_x + int(distance * ease)
        cy = start_y + int(3 * math.sin(i * 0.7))
        await client.send.Input.dispatchMouseEvent(
            {"type": "mouseMoved", "x": cx, "y": cy, "button": "left", "buttons": 1, "pointerType": "mouse"},
            session_id=session_id,
        )
        await asyncio.sleep(0.02 + 0.015 * math.sin(i * 0.3))

    end_x = start_x + distance
    await client.send.Input.dispatchMouseEvent(
        {"type": "mouseReleased", "x": end_x, "y": start_y, "button": "left", "clickCount": 1, "pointerType": "mouse"},
        session_id=session_id,
    )


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_captcha_")
    user_data_dir = os.path.join(os.path.dirname(__file__), "..", "xft_browser_profile")
    os.makedirs(user_data_dir, exist_ok=True)

    tool = BrowserTool(
        download_dir=download_dir,
        headless=False,
        user_data_dir=user_data_dir,
    )

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

        print("\nClick login button")
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
            print("Missing slider handle - checking if already past CAPTCHA")
        else:
            bottom_src = await page.evaluate("""() => { const img = document.querySelector('.bottomImage'); return img ? img.src : null; }""")
            drag_src = await page.evaluate("""() => { const img = document.querySelector('.dragImage'); return img ? img.src : null; }""")

            if bottom_src and drag_src:
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

                gap_x_in_bg, piece_min_x, piece_min_y = find_gap_position(bottom_img, drag_img)
                sx, sy = slider_handle["x"], slider_handle["y"]
                print(f"Slider handle at ({sx}, {sy})")

                RATIO = 0.94
                total_piece_movement = gap_x_in_bg - piece_min_x
                total_drag = round(total_piece_movement / RATIO)
                print(f"\nTotal drag needed: {total_drag}px (gap={gap_x_in_bg}, piece_offset={piece_min_x}, ratio={RATIO})")

                print(f"\nPerforming single drag of {total_drag}px via CDP...")
                await cdp_drag(page, sx, sy, total_drag, steps=40)
                print("Drag complete")
                await asyncio.sleep(3.0)

        # Check post-CAPTCHA state
        print("\n" + "=" * 60)
        print("Checking post-CAPTCHA state")
        print("=" * 60)

        page = await tool._maybe_get_page()
        if not page:
            print("No page available")
            return

        dom_state = await page.evaluate("""() => {
            const captcha = document.querySelector('.xftImageVerify');
            const hasCaptcha = captcha && captcha.style.display !== 'none';

            // Security verification screen
            const hasSmsVerify = document.body.innerText.includes('短信验证');
            const hasFaceVerify = document.body.innerText.includes('人脸验证');
            const hasSafeVerify = document.body.innerText.includes('安全验证');

            // Find SMS verify button
            const allEls = Array.from(document.querySelectorAll('body *'));
            const smsBtn = allEls.find(b => {
                const text = (b.innerText || b.textContent || '').trim();
                return text === '去验证' && b.offsetParent !== null;
            });
            const smsBtnPos = smsBtn ? {
                x: Math.round(smsBtn.getBoundingClientRect().left + smsBtn.getBoundingClientRect().width/2),
                y: Math.round(smsBtn.getBoundingClientRect().top + smsBtn.getBoundingClientRect().height/2)
            } : null;

            return JSON.stringify({
                hasCaptcha,
                hasSmsVerify,
                hasFaceVerify,
                hasSafeVerify,
                smsBtnPos,
                bodyText: document.body.innerText.slice(0, 500)
            });
        }""")
        if isinstance(dom_state, str):
            dom_state = json.loads(dom_state)

        print(f"Post-CAPTCHA state: {json.dumps(dom_state, ensure_ascii=False, indent=2)}")

        if dom_state.get("hasCaptcha"):
            print("[RESULT] CAPTCHA still visible - solve may have failed")
            screenshot_b64 = await page.screenshot(format="png")
            with open("xft_after_drag.png", "wb") as f:
                f.write(base64.b64decode(screenshot_b64))
            return

        if dom_state.get("hasSafeVerify"):
            print("\n[SECURITY] Device security verification required")
            if dom_state.get("hasSmsVerify") and dom_state.get("smsBtnPos"):
                print("SMS verification available. Clicking '去验证' to send SMS...")
                pos = dom_state["smsBtnPos"]
                result = await tool({"action": "click", "coordinate": [pos["x"], pos["y"]]})
                print(f"Click result: {result.output}")
                await asyncio.sleep(2.0)
                print("\n>>> SMS code sent to 18584828398")
                print(">>> Please provide the SMS verification code to continue login.")
                print(">>> (Script will wait here for manual intervention)")
                # Keep browser open for manual input
                print("\nKeeping browser open for 120s for manual SMS entry...")
                await asyncio.sleep(120.0)
            else:
                print("SMS verification button not found.")
                print("Keeping browser open for 30s for manual inspection...")
                await asyncio.sleep(30.0)
            return

        # Check if already logged in
        result = await tool({"action": "extract", "instruction": "get text"})
        text = result.output
        print(f"\nPage text: {text[:600]}")

        DASHBOARD_KEYWORDS = ["工作台", "薪税管家", "电子合同", "人员管理", "个税服务", "发薪台"]
        is_dashboard = any(kw in text for kw in DASHBOARD_KEYWORDS)

        if is_dashboard:
            print("[RESULT] Login successful! Dashboard loaded.")
        elif "登录成功" in text or "验证成功" in text:
            print("[RESULT] Login successful!")
        else:
            print("[RESULT] Unknown state - check screenshot")
            screenshot_b64 = await page.screenshot(format="png")
            with open("xft_after_drag.png", "wb") as f:
                f.write(base64.b64decode(screenshot_b64))
            return

        # Post-login exploration
        print("\n" + "=" * 60)
        print("Post-login exploration: ? icon -> 在线客服")
        print("=" * 60)

        page = await tool._maybe_get_page()

        # Step 1: Scan top bar for icon and image elements
        print("\n--- Step 1: Scanning top bar for CS icon ---")
        cs_candidates = None
        if page:
            cs_candidates = await page.evaluate("""() => {
                const viewportW = window.innerWidth;
                const all = Array.from(document.querySelectorAll('body *'));
                const candidates = [];
                for (const el of all) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5 || r.top > 80 || r.left < viewportW * 0.5) continue;
                    if (el.offsetParent === null) continue;
                    const style = window.getComputedStyle(el);
                    const text = (el.innerText || el.textContent || '').trim().slice(0, 20);
                    const cls = typeof el.className === 'string' ? el.className : '';
                    // Check for icon indicators
                    const hasIconClass = /icon|svg|help|service|kefu|kf|support|custom/i.test(cls);
                    const isImg = el.tagName === 'IMG';
                    const isSvg = el.tagName === 'svg' || el.closest('svg') !== null;
                    const hasBgImage = style.backgroundImage && style.backgroundImage !== 'none';
                    const isCursorPointer = style.cursor === 'pointer';
                    if (hasIconClass || isImg || isSvg || hasBgImage || isCursorPointer) {
                        candidates.push({
                            tag: el.tagName,
                            class: cls.slice(0, 60),
                            text: text,
                            title: el.title || '',
                            x: Math.round(r.left + r.width/2),
                            y: Math.round(r.top + r.height/2),
                            w: Math.round(r.width),
                            h: Math.round(r.height),
                            hasIconClass, isImg, isSvg, hasBgImage, isCursorPointer
                        });
                    }
                }
                // Deduplicate
                const deduped = [];
                for (const el of candidates) {
                    if (!deduped.some(d => Math.abs(d.x - el.x) < 5 && Math.abs(d.y - el.y) < 5)) {
                        deduped.push(el);
                    }
                }
                return JSON.stringify(deduped.slice(0, 20));
            }""")
            if isinstance(cs_candidates, str):
                try:
                    cs_candidates = json.loads(cs_candidates)
                except json.JSONDecodeError:
                    cs_candidates = []

        if cs_candidates:
            print(f"Found {len(cs_candidates)} icon/image candidates in top-right:")
            for el in cs_candidates:
                flags = []
                if el.get('hasIconClass'): flags.append('iconCls')
                if el.get('isImg'): flags.append('img')
                if el.get('isSvg'): flags.append('svg')
                if el.get('hasBgImage'): flags.append('bgImg')
                if el.get('isCursorPointer'): flags.append('pointer')
                print(f"  ({el['x']},{el['y']}) {el['w']}x{el['h']} {el['tag']} text='{el['text']}' class='{el['class'][:30]}' [{','.join(flags)}]")

        # Try each candidate until we find one that reveals 在线客服
        cs_found = False
        clicked_icon = None
        for el in (cs_candidates or []):
            # Skip user profile related
            cls = el.get('class', '').lower()
            if any(k in cls for k in ['user', 'logo', 'avatar', 'extend', 'arrow', 'input', 'suffix']):
                continue
            # Skip large text nav items
            if el['w'] > 80 and el['h'] > 30 and el.get('text'):
                continue
            print(f"\nTrying candidate at ({el['x']},{el['y']}) class='{el['class'][:30]}'")
            result = await tool({"action": "click", "coordinate": [el["x"], el["y"]]})
            print(f"Click result: {result.output}")
            await asyncio.sleep(1.5)
            clicked_icon = el
            # Check if page now has 在线客服
            page = await tool._maybe_get_page()
            if page:
                has_cs = await page.evaluate("""() => document.body.innerText.includes('在线客服')""")
                if has_cs:
                    print("[FOUND] 在线客服 text is now visible!")
                    cs_found = True
                    break
                # Also check if any modal/popup appeared
                modal_check = await page.evaluate("""() => {
                    const modals = document.querySelectorAll('.ant-modal, .modal, [class*="dialog"], [class*="popup"]');
                    return modals.length;
                }""")
                if modal_check > 0:
                    print(f"[MODAL] {modal_check} modal/popup element(s) detected")
            # Take screenshot for debugging
            if page:
                screenshot_b64 = await page.screenshot(format="png")
                with open(f"xft_cs_try_{el['x']}_{el['y']}.png", "wb") as f:
                    f.write(base64.b64decode(screenshot_b64))

        if not cs_found and cs_candidates:
            print("\n[WARNING] No click revealed 在线客服. Trying text search fallback...")
            result = await tool({"action": "click", "target_text": "客服"})
            print(f"Text fallback click: {result.output}")
            await asyncio.sleep(1.5)
            page = await tool._maybe_get_page()
            if page:
                has_cs = await page.evaluate("""() => document.body.innerText.includes('在线客服')""")
                if has_cs:
                    cs_found = True
        elif not cs_candidates:
            print("No candidates found, trying target_text='客服'")
            result = await tool({"action": "click", "target_text": "客服"})
            print(f"Click result: {result.output}")
            await asyncio.sleep(1.5)
            page = await tool._maybe_get_page()
            if page:
                has_cs = await page.evaluate("""() => document.body.innerText.includes('在线客服')""")
                if has_cs:
                    cs_found = True

        # Screenshot after clicking ?
        if page:
            screenshot_b64 = await page.screenshot(format="png")
            with open("xft_after_help_click.png", "wb") as f:
                f.write(base64.b64decode(screenshot_b64))
            print("Saved screenshot to xft_after_help_click.png")

        # Step 2: Click "在线客服"
        print("\n--- Step 2: Clicking 在线客服 ---")
        if cs_found:
            result = await tool({"action": "click", "target_text": "在线客服"})
            print(f"Click result: {result.output}")
        else:
            print("Skipping 在线客服 click - text was not revealed by any icon click")
        await asyncio.sleep(2.0)

        if page:
            screenshot_b64 = await page.screenshot(format="png")
            with open("xft_after_online_service.png", "wb") as f:
                f.write(base64.b64decode(screenshot_b64))
            print("Saved screenshot to xft_after_online_service.png")

        # Get final page text
        result = await tool({"action": "extract", "instruction": "get text"})
        print(f"\nFinal page text: {result.output[:600]}")

        print("\nKeeping browser open for 15s...")
        await asyncio.sleep(15.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
