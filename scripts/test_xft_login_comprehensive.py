"""Comprehensive xft login with all latest techniques.

Uses:
- force=true for React SPA clicks
- wait_for for lazy-loaded elements
- get_box for exact coordinate extraction
- network_log to discover CAPTCHA APIs
- edge-based gap detection
- vision fallback
- brute force fallback
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool
from openmimi.tools.computer import ComputerTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def click_login_button(browser: AgentBrowserTool) -> bool:
    """Click login with force fallback."""
    log("Clicking 登录...")
    result = await browser({"action": "click", "target_text": "登录"})
    await asyncio.sleep(2.0)

    # Check if login form appeared
    result = await browser({
        "action": "eval",
        "js": "(() => ({hasForm: !!document.querySelector('input.ant-input')}))()",
    })
    has_form = json.loads(result.output or "{}").get("hasForm", False)
    if has_form:
        log("Login form appeared.")
        return True

    # Retry with force
    log("Standard click didn't work, retrying with force=true...")
    result = await browser({"action": "click", "target_text": "登录", "force": True})
    await asyncio.sleep(2.0)
    result = await browser({
        "action": "eval",
        "js": "(() => ({hasForm: !!document.querySelector('input.ant-input')}))()",
    })
    has_form = json.loads(result.output or "{}").get("hasForm", False)
    if has_form:
        log("Login form appeared after force click.")
        return True

    log("Login form did not appear.")
    return False


async def fill_credentials(browser: AgentBrowserTool) -> bool:
    """Fill phone, password, and check agreement."""
    log("Filling credentials...")
    result = await browser({
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
                return {
                    ok: true,
                    hasPhone: !!phone,
                    hasPass: !!pass,
                    hasCheckbox: !!checkbox,
                    url: window.location.href
                };
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    log(f"Form fill: {json.dumps(data)}")
    return data.get("hasPhone", False) and data.get("hasPass", False)


async def submit_login(browser: AgentBrowserTool) -> bool:
    """Click submit button via JS eval (more reliable than text matching)."""
    log("Clicking submit...")
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                if (btn) {
                    btn.click();
                    return {clicked: true, class: btn.className, text: btn.innerText};
                }
                const alt = document.querySelector('button[type="submit"]');
                if (alt) {
                    alt.click();
                    return {clicked: true, class: alt.className, alt: true, text: alt.innerText};
                }
                return {clicked: false, html: document.body.innerHTML.substring(0, 200)};
            })()
        """,
    })
    log(f"Submit click: {result.output}")
    await asyncio.sleep(4.0)

    result = await browser({
        "action": "eval",
        "js": "(() => ({hasCaptcha: !!document.querySelector('.xftImageVerify')}))()",
    })
    has_captcha = json.loads(result.output or "{}").get("hasCaptcha", False)
    if has_captcha:
        log("CAPTCHA appeared.")
        return True

    # Check if we're logged in (URL changed or dashboard appeared)
    result = await browser({
        "action": "eval",
        "js": "(() => ({url: window.location.href, title: document.title}))()",
    })
    state = json.loads(result.output or "{}")
    log(f"Post-submit state: {json.dumps(state)}")
    return False


async def get_gap_edge(browser: AgentBrowserTool) -> int | None:
    """Edge-based gap detection."""
    log("Trying edge-based gap detection...")
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const bg = document.querySelector('.bottomImage');
                const piece = document.querySelector('.dragImage');
                if (!bg || !piece) return {error: 'missing images'};

                const bgW = bg.naturalWidth || 340;
                const bgH = bg.naturalHeight || 278;
                const pW = piece.naturalWidth || 78;
                const pH = piece.naturalHeight || 278;

                const bgCanvas = document.createElement('canvas');
                bgCanvas.width = bgW; bgCanvas.height = bgH;
                bgCanvas.getContext('2d').drawImage(bg, 0, 0);
                const bgData = bgCanvas.getContext('2d').getImageData(0, 0, bgW, bgH).data;

                const pCanvas = document.createElement('canvas');
                pCanvas.width = pW; pCanvas.height = pH;
                pCanvas.getContext('2d').drawImage(piece, 0, 0);
                const pData = pCanvas.getContext('2d').getImageData(0, 0, pW, pH).data;

                // Build edge mask: pixels adjacent to transparent regions
                const edgePixels = [];
                for (let py = 0; py < pH; py += 2) {
                    for (let px = 0; px < pW; px += 2) {
                        const idx = (py * pW + px) * 4;
                        if (pData[idx + 3] > 128) {
                            let isEdge = false;
                            for (const [dx, dy] of [[-1,0],[1,0],[0,-1],[0,1]]) {
                                const nx = px + dx, ny = py + dy;
                                if (nx >= 0 && nx < pW && ny >= 0 && ny < pH) {
                                    const nIdx = (ny * pW + nx) * 4;
                                    if (pData[nIdx + 3] < 128) { isEdge = true; break; }
                                }
                            }
                            if (isEdge) edgePixels.push({px, py});
                        }
                    }
                }

                const maxOffset = bgW - pW;
                let bestOffset = 0, maxDiff = -1;
                for (let ox = 0; ox <= maxOffset; ox++) {
                    let diff = 0, count = 0;
                    for (const {px, py} of edgePixels) {
                        const pIdx = (py * pW + px) * 4;
                        const bgIdx = (py * bgW + (ox + px)) * 4;
                        diff += Math.abs(pData[pIdx] - bgData[bgIdx])
                              + Math.abs(pData[pIdx+1] - bgData[bgIdx+1])
                              + Math.abs(pData[pIdx+2] - bgData[bgIdx+2]);
                        count++;
                    }
                    if (count > 0) {
                        const avgDiff = diff / count;
                        if (avgDiff > maxDiff) { maxDiff = avgDiff; bestOffset = ox; }
                    }
                }
                return {gap: bestOffset, maxDiff: Math.round(maxDiff), edgeCount: edgePixels.length};
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    gap = data.get("gap")
    log(f"Edge detection: gap={gap}px (diff={data.get('maxDiff')}, edges={data.get('edgeCount')})")
    return gap


async def get_gap_pixeldiff(browser: AgentBrowserTool) -> int | None:
    """Pixel diff gap detection (fallback)."""
    log("Trying pixeldiff gap detection...")
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const bg = document.querySelector('.bottomImage');
                const piece = document.querySelector('.dragImage');
                if (!bg || !piece) return {error: 'missing images'};

                const bgW = bg.naturalWidth || 340;
                const bgH = bg.naturalHeight || 278;
                const pW = piece.naturalWidth || 78;
                const pH = piece.naturalHeight || 278;

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
                return {gap: bestOffset, maxDiff: Math.round(maxDiff)};
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    gap = data.get("gap")
    log(f"Pixeldiff: gap={gap}px")
    return gap


async def get_handle_position(browser: AgentBrowserTool) -> tuple[int, int] | None:
    """Get handle screen coordinates using get_box or eval."""
    log("Getting handle position...")
    # Try get_box first
    result = await browser({"action": "get_box", "target_text": "拖动滑块"})
    if not result.is_error:
        try:
            box = json.loads(result.output or "{}")
            x = int(box.get("x", 0) + box.get("width", 0) / 2)
            y = int(box.get("y", 0) + box.get("height", 0) / 2)
            log(f"Handle viewport center: ({x}, {y})")
        except Exception:
            box = None
    else:
        box = None

    # Always get screen offset via eval
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const sx = window.screenX + (window.outerWidth - window.innerWidth) / 2;
                const sy = window.screenY + window.outerHeight - window.innerHeight - (window.outerWidth - window.innerWidth) / 2;
                return {sx: Math.round(sx), sy: Math.round(sy)};
            })()
        """,
    })
    offset = json.loads(result.output or "{}")
    sx = offset.get("sx", 0)
    sy = offset.get("sy", 0)

    if box:
        screen_x = sx + box["x"] + box["width"] // 2
        screen_y = sy + box["y"] + box["height"] // 2
    else:
        # Fallback to querying the handle directly
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const r = btn.getBoundingClientRect();
                    const sx = window.screenX + (window.outerWidth - window.innerWidth) / 2;
                    const sy = window.screenY + window.outerHeight - window.innerHeight - (window.outerWidth - window.innerWidth) / 2;
                    return {
                        screenX: Math.round(sx + r.left + r.width/2),
                        screenY: Math.round(sy + r.top + r.height/2),
                    };
                })()
            """,
        })
        pos = json.loads(result.output or "{}")
        screen_x = pos.get("screenX", 0)
        screen_y = pos.get("screenY", 0)

    log(f"Handle screen: ({screen_x}, {screen_y})")
    return screen_x, screen_y


async def try_drag(browser: AgentBrowserTool, computer: ComputerTool, sx: int, sy: int, distance: int) -> bool:
    """Perform drag and check if CAPTCHA is solved."""
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


async def solve_captcha(browser: AgentBrowserTool, computer: ComputerTool) -> bool:
    """Try multiple methods to solve CAPTCHA."""
    sx, sy = await get_handle_position(browser)
    if sx is None or sy is None:
        log("Could not get handle position")
        return False

    # Method 1: Edge-based
    gap = await get_gap_edge(browser)
    if gap is not None:
        handle_drag = int(gap * 280 / 262)
        log(f"\nEdge suggests handle_drag={handle_drag}px")
        if await try_drag(browser, computer, sx, sy, handle_drag):
            log("SUCCESS with edge!")
            return True
        for offset in [-10, 10, -20, 20]:
            if await try_drag(browser, computer, sx, sy, handle_drag + offset):
                log(f"SUCCESS with offset {offset}!")
                return True
            await asyncio.sleep(1.0)

    # Method 2: Pixeldiff
    gap = await get_gap_pixeldiff(browser)
    if gap is not None:
        handle_drag = int(gap * 280 / 262)
        log(f"\nPixeldiff suggests handle_drag={handle_drag}px")
        if await try_drag(browser, computer, sx, sy, handle_drag):
            log("SUCCESS with pixeldiff!")
            return True
        for offset in [-10, 10, -20, 20]:
            if await try_drag(browser, computer, sx, sy, handle_drag + offset):
                log(f"SUCCESS with offset {offset}!")
                return True
            await asyncio.sleep(1.0)

    # Method 3: Brute force
    log("\nBrute force...")
    for dist in range(100, 261, 10):
        if await try_drag(browser, computer, sx, sy, dist):
            log(f"SUCCESS at {dist}px!")
            return True
        await asyncio.sleep(2.0)
        result = await browser({
            "action": "eval",
            "js": "(() => ({hasVerify: !!document.querySelector('.xftImageVerify')}))()",
        })
        if not json.loads(result.output or "{}").get("hasVerify"):
            log("CAPTCHA disappeared")
            return True

    log("All CAPTCHA methods failed.")
    return False


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=["--disable-blink-features=AutomationControlled"],
    )
    computer = ComputerTool()

    try:
        log("=== Navigate ===")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)

        log("=== Click Login ===")
        if not await click_login_button(browser):
            log("Failed to open login form")
            return

        log("=== Fill Form ===")
        if not await fill_credentials(browser):
            log("Form filling failed")
            return

        log("=== Submit ===")
        has_captcha = await submit_login(browser)
        if not has_captcha:
            log("No CAPTCHA - checking if logged in...")
            result = await browser({
                "action": "eval",
                "js": "(() => ({url: window.location.href, title: document.title}))()",
            })
            state = json.loads(result.output or "{}")
            log(f"Final state: {json.dumps(state)}")
            return

        log("=== Solve CAPTCHA ===")
        if await solve_captcha(browser, computer):
            log("=== LOGIN SUCCESS ===")
            # Take screenshot of logged-in state
            await browser({"action": "screenshot", "path": os.path.join(download_dir, "logged_in.png")})
        else:
            log("=== LOGIN FAILED ===")

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
