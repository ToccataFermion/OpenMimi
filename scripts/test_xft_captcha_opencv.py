"""Solve xft slider CAPTCHA using OpenCV template matching on the puzzle images."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool
from openmimi.tools.computer import ComputerTool

try:
    import cv2
    import numpy as np
    HAS_CV = True
except ImportError:
    HAS_CV = False

try:
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def find_browser_window():
    candidates = []
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "Chrome" in title:
                rect = win32gui.GetWindowRect(hwnd)
                candidates.append((hwnd, title, rect))
        return True
    win32gui.EnumWindows(callback, None)
    return candidates[-1] if candidates else None


def focus_window(hwnd):
    try:
        win32gui.SetForegroundWindow(hwnd)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception as exc:
        log(f"  Focus warning: {exc}")


def solve_gap_distance(bg_b64: str, piece_b64: str) -> int | None:
    """Use OpenCV template matching to find puzzle piece position in background."""
    if not HAS_CV:
        return None

    # Decode base64 images
    bg_data = base64.b64decode(bg_b64.split(",")[1] if "," in bg_b64 else bg_b64)
    piece_data = base64.b64decode(piece_b64.split(",")[1] if "," in piece_b64 else piece_b64)

    bg_arr = np.frombuffer(bg_data, np.uint8)
    piece_arr = np.frombuffer(piece_data, np.uint8)

    bg = cv2.imdecode(bg_arr, cv2.IMREAD_COLOR)
    piece = cv2.imdecode(piece_arr, cv2.IMREAD_COLOR)

    if bg is None or piece is None:
        log("  Failed to decode images")
        return None

    # Save images for debugging
    cv2.imwrite("data/captcha_bg.png", bg)
    cv2.imwrite("data/captcha_piece.png", piece)
    log(f"  Saved images: bg={bg.shape}, piece={piece.shape}")

    # The piece image includes the left edge; we need to match it in the background
    # where the gap is. Try multiple approaches.
    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)

    # Method 1: Direct template matching on grayscale
    result1 = cv2.matchTemplate(bg_gray, piece_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val1, _, max_loc1 = cv2.minMaxLoc(result1)
    log(f"  Grayscale match: x={max_loc1[0]}, conf={max_val1:.3f}")

    # Method 2: Canny edge detection
    bg_edges = cv2.Canny(bg_gray, 50, 150)
    piece_edges = cv2.Canny(piece_gray, 50, 150)
    result2 = cv2.matchTemplate(bg_edges, piece_edges, cv2.TM_CCOEFF_NORMED)
    _, max_val2, _, max_loc2 = cv2.minMaxLoc(result2)
    log(f"  Edge match: x={max_loc2[0]}, conf={max_val2:.3f}")

    # Method 3: Binary threshold (useful if there's a clear shape difference)
    _, bg_bin = cv2.threshold(bg_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, piece_bin = cv2.threshold(piece_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    result3 = cv2.matchTemplate(bg_bin, piece_bin, cv2.TM_CCOEFF_NORMED)
    _, max_val3, _, max_loc3 = cv2.minMaxLoc(result3)
    log(f"  Binary match: x={max_loc3[0]}, conf={max_val3:.3f}")

    # Use the method with highest confidence
    methods = [(max_val1, max_loc1[0]), (max_val2, max_loc2[0]), (max_val3, max_loc3[0])]
    best_val, best_x = max(methods, key=lambda x: x[0])
    log(f"  Best match: x={best_x}, confidence={best_val:.3f}")

    return best_x


async def main() -> None:
    if not HAS_CV:
        log("OpenCV not available, aborting")
        return
    if not HAS_WIN32:
        log("win32gui not available, aborting")
        return

    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=["--disable-blink-features=AutomationControlled"],
    )
    computer = ComputerTool()

    try:
        log("Step 1: Navigate to xft")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)

        log("Step 2: Click login tab")
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 3: Fill credentials")
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const inputs = Array.from(document.querySelectorAll('input.ant-input'));
                    const phone = inputs.find(el => el.type === 'text');
                    const pass = inputs.find(el => el.type === 'password');
                    const checkbox = document.querySelector('input.ant-checkbox-input');
                    function setReactValue(element, value) {
                        const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        valueSetter.call(element, value);
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    if (phone) setReactValue(phone, '18584828398');
                    if (pass) setReactValue(pass, 'Liszt123');
                    if (checkbox && !checkbox.checked) checkbox.click();
                    return {ok: true};
                })()
            """,
        })
        await asyncio.sleep(0.5)

        log("Step 4: Click login to trigger CAPTCHA")
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                    if (btn) {
                        ['mousedown', 'mouseup', 'click'].forEach(type => {
                            btn.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                        });
                    }
                    return {clicked: !!btn};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        log("Step 5: Get CAPTCHA images and coordinates")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const bg = document.querySelector('.bottomImage');

                    function getSrc(el) {
                        if (!el) return null;
                        if (el.src) return el.src;
                        const style = window.getComputedStyle(el);
                        const m = style.backgroundImage.match(/url\(["']?(data:image\/[^"']+)["']?\)/);
                        return m ? m[1] : null;
                    }

                    function rectInfo(el) {
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return {left: r.left, top: r.top, width: r.width, height: r.height};
                    }

                    return {
                        hasButton: !!btn,
                        hasDrag: !!drag,
                        hasBg: !!bg,
                        btnRect: rectInfo(btn),
                        dragRect: rectInfo(drag),
                        bgRect: rectInfo(bg),
                        bgSrc: getSrc(bg),
                        dragSrc: getSrc(drag),
                        screenX: window.screenX,
                        screenY: window.screenY,
                        innerWidth: window.innerWidth,
                        innerHeight: window.innerHeight,
                        outerWidth: window.outerWidth,
                        outerHeight: window.outerHeight,
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"  CAPTCHA data keys: {list(data.keys())}")

        if not data.get("hasButton"):
            log("  No CAPTCHA appeared")
            return

        bg_src = data.get("bgSrc")
        drag_src = data.get("dragSrc")

        if not bg_src or not drag_src:
            log("  Could not extract image sources")
            return

        log(f"  bgSrc prefix: {bg_src[:50]}...")
        log(f"  dragSrc prefix: {drag_src[:50]}...")

        # Use OpenCV to find the gap position
        gap_x = solve_gap_distance(bg_src, drag_src)
        if gap_x is None:
            log("  OpenCV analysis failed")
            return

        # Calculate screen coordinates
        sx = data.get("screenX", 0)
        sy = data.get("screenY", 0)
        ow = data.get("outerWidth", 1280)
        oh = data.get("outerHeight", 800)
        iw = data.get("innerWidth", 1280)
        ih = data.get("innerHeight", 800)
        left_frame = (ow - iw) // 2
        top_frame = oh - ih - left_frame

        btn_rect = data.get("btnRect")
        if not btn_rect:
            log("  No button rect")
            return

        # Start: center of drag button
        start_vx = btn_rect["left"] + btn_rect["width"] / 2
        start_vy = btn_rect["top"] + btn_rect["height"] / 2
        start_sx = int(sx + left_frame + start_vx)
        start_sy = int(sy + top_frame + start_vy)

        # End: bg left edge + gap position + half button width (so button center aligns with gap)
        bg_rect = data.get("bgRect", {})
        bg_left = bg_rect.get("left", 0)
        end_vx = bg_left + gap_x + btn_rect["width"] / 2
        end_vy = start_vy
        end_sx = int(sx + left_frame + end_vx)
        end_sy = int(sy + top_frame + end_vy)

        log(f"  Viewport drag: ({start_vx:.0f}, {start_vy:.0f}) -> ({end_vx:.0f}, {end_vy:.0f})")
        log(f"  Screen drag:   ({start_sx}, {start_sy}) -> ({end_sx}, {end_sy})")

        log("Step 6: Focus browser window")
        win_info = find_browser_window()
        if win_info:
            focus_window(win_info[0])
            await asyncio.sleep(0.5)

        log("Step 7: OS-level drag using ComputerTool")
        await computer({
            "action": "mouse_drag",
            "x": start_sx,
            "y": start_sy,
            "end_x": end_sx,
            "end_y": end_sy,
            "button": "left",
            "steps": 40,
        })
        await asyncio.sleep(2.0)

        log("Step 8: Check result")
        result = await browser({"action": "snapshot"})
        text = result.output or ""
        if "手机号登录" not in text and "密码登录" not in text:
            log("  CAPTCHA solved! Page changed.")
        else:
            log("  CAPTCHA may have failed. Page still shows login.")
        log(f"  Snapshot preview: {text[:300]}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
