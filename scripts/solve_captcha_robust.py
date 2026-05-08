"""Robust CAPTCHA solver with proper tab management and retry logic."""
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


async def robust_login(browser: AgentBrowserTool) -> bool:
    """Login with retry logic. Returns True if CAPTCHA appeared."""
    log("Navigating...")
    await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
    await asyncio.sleep(3.0)

    log("Clicking login...")
    await browser({"action": "click", "target_text": "登录"})
    await asyncio.sleep(3.0)

    # Switch to login tab (t2) if needed
    tabs = await browser({"action": "tab"})
    log(f"Tabs: {tabs.output}")
    await asyncio.sleep(1.0)

    log("Filling form...")
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
    if not data.get("hasPhone") or not data.get("hasPass"):
        log("Form elements not found, retrying on t2...")
        await browser({"action": "tab", "target": "t2"})
        await asyncio.sleep(2.0)
        # Retry form fill
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
                        url: window.location.href
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"Form fill retry: {json.dumps(data)}")

    await asyncio.sleep(0.5)

    log("Clicking login button...")
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                if (btn) { btn.click(); return {clicked: true, class: btn.className}; }
                // Try alternative selectors
                const alt = document.querySelector('button[type="submit"]');
                if (alt) { alt.click(); return {clicked: true, class: alt.className, alt: true}; }
                return {clicked: false, html: document.body.innerHTML.substring(0, 200)};
            })()
        """,
    })
    log(f"Login click: {result.output}")
    await asyncio.sleep(4.0)

    # Check if CAPTCHA appeared
    result = await browser({
        "action": "eval",
        "js": "(() => ({hasCaptcha: !!document.querySelector('.xftImageVerify')}))()",
    })
    has_captcha = json.loads(result.output or "{}").get("hasCaptcha", False)
    log(f"CAPTCHA present: {has_captcha}")
    return has_captcha


async def get_gap_by_pixeldiff(browser: AgentBrowserTool) -> int | None:
    """Use canvas pixel comparison to estimate gap."""
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
                let bestOffset = 0;
                let maxDiff = -1;

                for (let ox = 0; ox <= maxOffset; ox++) {
                    let diff = 0;
                    let count = 0;
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
                        if (avgDiff > maxDiff) {
                            maxDiff = avgDiff;
                            bestOffset = ox;
                        }
                    }
                }
                return {gap: bestOffset, maxDiff: Math.round(maxDiff)};
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    log(f"Pixeldiff result: {json.dumps(data)}")
    return data.get("gap")


async def try_drag(browser: AgentBrowserTool, computer: ComputerTool, distance: int) -> bool:
    """Attempt a drag and check if CAPTCHA is solved."""
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
    sx, sy = pos["screenX"], pos["screenY"]

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
        has_captcha = await robust_login(browser)
        if not has_captcha:
            log("No CAPTCHA appeared. Login may have succeeded without it or failed.")
            # Take screenshot to see current state
            await browser({"action": "screenshot", "path": os.path.join(download_dir, "no_captcha.png")})
            return

        # Method 1: Pixel diff
        gap = await get_gap_by_pixeldiff(browser)
        if gap is not None:
            handle_drag = int(gap * 280 / 262)
            log(f"\nPixeldiff suggests gap={gap}px, handle_drag={handle_drag}px")
            if await try_drag(browser, computer, handle_drag):
                log("SUCCESS with pixeldiff!")
                return
            # Try nearby
            for offset in [-10, 10, -20, 20]:
                if await try_drag(browser, computer, handle_drag + offset):
                    log(f"SUCCESS with offset {offset}!")
                    return
                await asyncio.sleep(1.0)

        # Method 2: Brute force with fresh instances
        log("\nPixeldiff failed. Trying brute force...")
        for dist in range(100, 261, 10):
            if await try_drag(browser, computer, dist):
                log(f"SUCCESS at {dist}px!")
                return
            # CAPTCHA auto-resets, wait for it
            await asyncio.sleep(2.0)
            # Verify CAPTCHA is still there
            result = await browser({
                "action": "eval",
                "js": "(() => ({hasVerify: !!document.querySelector('.xftImageVerify')}))()",
            })
            if not json.loads(result.output or "{}").get("hasVerify"):
                log("CAPTCHA disappeared unexpectedly")
                return

        log("\nAll methods failed.")

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
