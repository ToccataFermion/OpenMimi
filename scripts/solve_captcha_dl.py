"""xft login with deep-learning-based CAPTCHA gap detection.

Uses captcha-recognizer (ONNX YOLO instance segmentation) on a screenshot
of the CAPTCHA modal to find the gap position, then drags with physics-based
acceleration/deceleration trajectory.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool
from openmimi.tools.computer import ComputerTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def login_flow(browser: AgentBrowserTool) -> bool:
    log("=== Navigate ===")
    await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
    await asyncio.sleep(3.0)

    log("=== Click Login ===")
    result = await browser({"action": "click", "target_text": "登录"})
    await asyncio.sleep(2.0)
    result = await browser({
        "action": "eval",
        "js": "(() => ({hasForm: !!document.querySelector('input.ant-input')}))()",
    })
    has_form = json.loads(result.output or "{}").get("hasForm", False)
    if not has_form:
        result = await browser({"action": "click", "target_text": "登录", "force": True})
        await asyncio.sleep(2.0)
        result = await browser({
            "action": "eval",
            "js": "(() => ({hasForm: !!document.querySelector('input.ant-input')}))()",
        })
        has_form = json.loads(result.output or "{}").get("hasForm", False)
    if not has_form:
        log("Failed to open login form")
        return False

    log("=== Fill Form ===")
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
                return {hasPhone: !!phone, hasPass: !!pass, hasCheckbox: !!checkbox};
            })()
        """,
    })

    log("=== Submit ===")
    await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                if (btn) { btn.click(); return {clicked: true}; }
                return {clicked: false};
            })()
        """,
    })
    await asyncio.sleep(4.0)

    result = await browser({
        "action": "eval",
        "js": "(() => ({hasCaptcha: !!document.querySelector('.xftImageVerify')}))()",
    })
    return json.loads(result.output or "{}").get("hasCaptcha", False)


async def get_gap_dl(browser: AgentBrowserTool, download_dir: str) -> int | None:
    """Use captcha-recognizer deep learning model on CAPTCHA screenshot."""
    try:
        from captcha_recognizer.slider import Slider
    except ImportError:
        log("captcha-recognizer not installed")
        return None

    log("Taking CAPTCHA screenshot for DL analysis...")
    screenshot_path = os.path.join(download_dir, "captcha_screenshot.png")
    result = await browser({"action": "screenshot", "path": screenshot_path})

    if not os.path.exists(screenshot_path):
        log("Screenshot not saved")
        return None

    # Try to crop to just the CAPTCHA modal area for better accuracy
    # First, get the modal position
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const modal = document.querySelector('.xftImageVerify, .imageVerify, [class*="imageVerify"]');
                if (!modal) return {error: 'modal not found'};
                const r = modal.getBoundingClientRect();
                return {left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height)};
            })()
        """,
    })
    modal_data = json.loads(result.output or "{}")
    if modal_data.get("error"):
        log(f"Modal detection: {modal_data.get('error')}")
        # Use full screenshot
        crop_path = screenshot_path
    else:
        left = modal_data.get("left", 0)
        top = modal_data.get("top", 0)
        width = modal_data.get("width", 0)
        height = modal_data.get("height", 0)
        log(f"Modal: left={left}, top={top}, width={width}, height={height}")

        # Crop screenshot to modal area
        try:
            from PIL import Image
            img = Image.open(screenshot_path)
            # Add some padding around the modal
            pad = 20
            crop_box = (
                max(0, left - pad),
                max(0, top - pad),
                min(img.width, left + width + pad),
                min(img.height, top + height + pad),
            )
            cropped = img.crop(crop_box)
            crop_path = os.path.join(download_dir, "captcha_cropped.png")
            cropped.save(crop_path)
            log(f"Cropped CAPTCHA saved to {crop_path}")
        except Exception as exc:
            log(f"Crop failed: {exc}")
            crop_path = screenshot_path

    # Run deep learning model
    try:
        slider = Slider()
        offset, conf = slider.identify_offset(crop_path)
        log(f"DL gap detection: offset={offset}px, confidence={conf:.3f}")
        if conf < 0.5:
            log("Confidence too low, skipping DL result")
            return None
        return int(offset)
    except Exception as exc:
        log(f"DL detection failed: {exc}")
        return None


async def get_gap_pixeldiff(browser: AgentBrowserTool) -> int | None:
    """Fallback pixeldiff gap detection."""
    log("Trying pixeldiff gap detection...")
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const bg = document.querySelector('.bottomImage');
                const piece = document.querySelector('.dragImage');
                if (!bg || !piece) return {error: 'missing images'};
                let attempts = 0;
                while (attempts < 30) {
                    const bgReady = bg.complete && (bg.naturalWidth || bg.width) > 0;
                    const pieceReady = piece.complete && (piece.naturalWidth || piece.width) > 0;
                    if (bgReady && pieceReady) break;
                    attempts++;
                }
                if (attempts >= 30) return {error: 'images not loaded'};
                const bgW = bg.naturalWidth || bg.width || 340;
                const bgH = bg.naturalHeight || bg.height || 278;
                const pW = piece.naturalWidth || piece.width || 78;
                const pH = piece.naturalHeight || piece.height || 278;
                const bgCanvas = document.createElement('canvas');
                bgCanvas.width = bgW; bgCanvas.height = bgH;
                bgCanvas.getContext('2d').drawImage(bg, 0, 0);
                const bgData = bgCanvas.getContext('2d').getImageData(0, 0, bgW, bgH).data;
                const pCanvas = document.createElement('canvas');
                pCanvas.width = pW; pCanvas.height = pH;
                pCanvas.getContext('2d').drawImage(piece, 0, 0);
                const pData = pCanvas.getContext('2d').getImageData(0, 0, pW, pH).data;
                const maxOffset = bgW - pW;
                let bestOffset = 0, maxDiff = -1;
                for (let ox = 0; ox <= maxOffset; ox++) {
                    let diff = 0, count = 0;
                    for (let py = 0; py < pH; py += 2) {
                        for (let px = 0; px < pW; px += 2) {
                            const pIdx = (py * pW + px) * 4;
                            if (pData[pIdx + 3] < 128) continue;
                            const bgIdx = (py * bgW + (ox + px)) * 4;
                            diff += Math.abs(pData[pIdx] - bgData[bgIdx])
                                  + Math.abs(pData[pIdx+1] - bgData[bgIdx+1])
                                  + Math.abs(pData[pIdx+2] - bgData[bgIdx+2]);
                            count++;
                        }
                    }
                    if (count > 0) {
                        const avgDiff = diff / count;
                        if (avgDiff > maxDiff) { maxDiff = avgDiff; bestOffset = ox; }
                    }
                }
                return {gap: bestOffset, maxDiff: Math.round(maxDiff), bgW, pW};
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    gap = data.get("gap")
    log(f"Pixeldiff: gap={gap}px (maxDiff={data.get('maxDiff')}, bgW={data.get('bgW')}, pW={data.get('pW')})")
    return gap


async def get_handle_position(browser: AgentBrowserTool) -> tuple[int, int] | None:
    log("Getting handle position...")
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.imageVerifyDragButton');
                const r = btn.getBoundingClientRect();
                const sx = window.screenX + (window.outerWidth - window.innerWidth) / 2;
                const sy = window.screenY + window.outerHeight - window.innerHeight - (window.outerWidth - window.innerWidth) / 2;
                return {screenX: Math.round(sx + r.left + r.width/2), screenY: Math.round(sy + r.top + r.height/2)};
            })()
        """,
    })
    pos = json.loads(result.output or "{}")
    sx, sy = pos.get("screenX", 0), pos.get("screenY", 0)
    log(f"Handle screen: ({sx}, {sy})")
    return sx, sy


async def try_drag(browser: AgentBrowserTool, computer: ComputerTool, sx: int, sy: int, distance: int) -> bool:
    if distance < 10:
        return False
    await browser({"action": "focus"})
    await asyncio.sleep(0.3)
    await computer({
        "action": "mouse_drag",
        "x": sx, "y": sy,
        "end_x": sx + distance, "end_y": sy,
        "steps": 80, "delay_ms": 25,
    })
    await asyncio.sleep(2.0)
    result = await browser({
        "action": "eval",
        "js": "(() => ({hasVerify: !!document.querySelector('.xftImageVerify')}))()",
    })
    solved = not json.loads(result.output or "{}").get("hasVerify", True)
    log(f"  Drag {distance}px: {'SOLVED' if solved else 'failed'}")
    return solved


async def solve_captcha(browser: AgentBrowserTool, computer: ComputerTool, download_dir: str) -> bool:
    sx, sy = await get_handle_position(browser)
    if sx is None or sy is None:
        return False

    # Method 1: Deep learning on screenshot
    gap = await get_gap_dl(browser, download_dir)
    if gap is not None and gap > 10:
        handle_drag = int(gap * 280 / 262)
        log(f"DL suggests handle_drag={handle_drag}px")
        for offset in [0, 10, -10, 20, -20]:
            if await try_drag(browser, computer, sx, sy, handle_drag + offset):
                log("SUCCESS with DL!")
                return True
            await asyncio.sleep(1.0)

    # Method 2: Pixeldiff fallback
    gap = await get_gap_pixeldiff(browser)
    if gap is not None and gap > 10:
        handle_drag = int(gap * 280 / 262)
        log(f"Pixeldiff suggests handle_drag={handle_drag}px")
        for offset in [0, 10, -10, 20, -20]:
            if await try_drag(browser, computer, sx, sy, handle_drag + offset):
                log("SUCCESS with pixeldiff!")
                return True
            await asyncio.sleep(1.0)

    # Method 3: Brute force
    log("Brute force fallback...")
    for dist in range(80, 261, 15):
        if await try_drag(browser, computer, sx, sy, dist):
            log(f"SUCCESS at {dist}px!")
            return True
        await asyncio.sleep(2.0)

    return False


async def explore_workbench(browser: AgentBrowserTool, download_dir: str) -> None:
    log("\n=== Exploring Workbench ===")
    await browser({"action": "screenshot", "path": os.path.join(download_dir, "workbench_01.png")})

    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const links = Array.from(document.querySelectorAll('a, button, [role="button"], .nav-item, .menu-item, [class*="menu"], [class*="nav"]'));
                return links.slice(0, 40).map(el => ({
                    tag: el.tagName,
                    text: (el.innerText || el.textContent || '').trim().substring(0, 40),
                    className: el.className,
                    href: el.href || null,
                }));
            })()
        """,
    })
    nav_items = json.loads(result.output or "[]")
    log(f"Found {len(nav_items)} navigation items")
    for i, item in enumerate(nav_items[:10]):
        log(f"  [{i}] {item.get('tag')} | {item.get('text')}")

    await browser({"action": "screenshot", "path": os.path.join(download_dir, "workbench_99.png")})


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_dl_")
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=["--disable-blink-features=AutomationControlled"],
    )
    computer = ComputerTool()

    try:
        has_captcha = await login_flow(browser)
        if not has_captcha:
            log("No CAPTCHA - checking login state...")
            result = await browser({
                "action": "eval",
                "js": "(() => ({url: window.location.href, title: document.title}))()",
            })
            log(f"State: {result.output}")
            return

        log("=== Solve CAPTCHA ===")
        if await solve_captcha(browser, computer, download_dir):
            log("=== LOGIN SUCCESS ===")
            await explore_workbench(browser, download_dir)
        else:
            log("=== LOGIN FAILED ===")

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
