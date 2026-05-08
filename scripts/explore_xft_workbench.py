"""Login to xft using proven comprehensive flow, then explore workbench."""
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


async def click_login_button(browser: AgentBrowserTool) -> bool:
    log("Clicking 登录...")
    result = await browser({"action": "click", "target_text": "登录"})
    await asyncio.sleep(2.0)
    result = await browser({
        "action": "eval",
        "js": "(() => ({hasForm: !!document.querySelector('input.ant-input')}))()",
    })
    has_form = json.loads(result.output or "{}").get("hasForm", False)
    if has_form:
        log("Login form appeared.")
        return True
    log("Retrying with force=true...")
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
    return False


async def fill_credentials(browser: AgentBrowserTool) -> bool:
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
                return {hasPhone: !!phone, hasPass: !!pass, hasCheckbox: !!checkbox};
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    log(f"Form fill: {json.dumps(data)}")
    return data.get("hasPhone", False) and data.get("hasPass", False)


async def submit_login(browser: AgentBrowserTool) -> bool:
    log("Clicking submit...")
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                if (btn) { btn.click(); return {clicked: true, text: btn.innerText}; }
                return {clicked: false};
            })()
        """,
    })
    log(f"Submit: {result.output}")
    await asyncio.sleep(4.0)
    result = await browser({
        "action": "eval",
        "js": "(() => ({hasCaptcha: !!document.querySelector('.xftImageVerify')}))()",
    })
    has_captcha = json.loads(result.output or "{}").get("hasCaptcha", False)
    if has_captcha:
        log("CAPTCHA appeared.")
        return True
    log("No CAPTCHA detected.")
    return False


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


async def get_gap_pixeldiff(browser: AgentBrowserTool) -> int | None:
    log("Trying pixeldiff gap detection...")
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const bg = document.querySelector('.bottomImage');
                const piece = document.querySelector('.dragImage');
                if (!bg || !piece) return {error: 'missing images'};

                // Wait for images to be fully loaded
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


async def solve_captcha(browser: AgentBrowserTool, computer: ComputerTool) -> bool:
    sx, sy = await get_handle_position(browser)
    if sx is None or sy is None:
        return False

    gap = await get_gap_pixeldiff(browser)
    if gap is not None and gap > 10:
        handle_drag = int(gap * 280 / 262)
        log(f"Pixeldiff suggests handle_drag={handle_drag}px")
        for offset in [0, 10, -10, 20, -20]:
            if await try_drag(browser, computer, sx, sy, handle_drag + offset):
                log("SUCCESS!")
                return True
            await asyncio.sleep(1.0)

    # Brute force fallback
    log("Brute force fallback...")
    for dist in range(80, 261, 15):
        if await try_drag(browser, computer, sx, sy, dist):
            log(f"SUCCESS at {dist}px!")
            return True
        await asyncio.sleep(2.0)

    return False


async def explore_workbench(browser: AgentBrowserTool, download_dir: str) -> None:
    log("\n=== Exploring Workbench ===")
    await browser({"action": "screenshot", "path": os.path.join(download_dir, "workbench_01_initial.png")})

    # Discover navigation items
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
    log(f"Navigation items ({len(nav_items)}):")
    for i, item in enumerate(nav_items):
        log(f"  [{i}] {item.get('tag')} | {item.get('text')} | {item.get('className', '')[:50]}")

    # Try clicking common workbench keywords
    for keyword in ["工作台", "首页", "我的", "Dashboard", "Workbench"]:
        result = await browser({
            "action": "eval",
            "js": f"""
                (() => {{
                    const el = Array.from(document.querySelectorAll('a, button, div, span, li')).find(
                        e => (e.innerText || e.textContent || '').trim().includes('{keyword}')
                    );
                    if (el) {{
                        el.click();
                        return {{clicked: true, text: (el.innerText || '').trim().substring(0, 40)}};
                    }}
                    return {{clicked: false}};
                }})()
            """,
        })
        data = json.loads(result.output or "{}")
        if data.get("clicked"):
            log(f"Clicked '{keyword}': {data.get('text')}")
            await asyncio.sleep(3.0)
            await browser({"action": "screenshot", "path": os.path.join(download_dir, f"workbench_02_{keyword}.png")})
            break

    # Check for data tables, cards, lists
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const tables = document.querySelectorAll('table');
                const cards = document.querySelectorAll('.card, [class*="card"], [class*="Card"]');
                const lists = document.querySelectorAll('.list, [class*="list"], [class*="List"]');
                return {
                    tableCount: tables.length,
                    cardCount: cards.length,
                    listCount: lists.length,
                    tableHeaders: Array.from(tables).slice(0, 3).map(t => Array.from(t.querySelectorAll('th')).map(th => th.innerText.trim())),
                };
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    log(f"Page structure: {json.dumps(data, ensure_ascii=False)}")

    await browser({"action": "screenshot", "path": os.path.join(download_dir, "workbench_99_final.png")})
    log(f"Screenshots saved to {download_dir}")


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_wb_")
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
        if has_captcha:
            log("=== Solve CAPTCHA ===")
            if await solve_captcha(browser, computer):
                log("=== LOGIN SUCCESS ===")
            else:
                log("=== LOGIN FAILED (CAPTCHA) ===")
                return
        else:
            log("=== No CAPTCHA - may be logged in ===")

        # Check login state
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const avatar = document.querySelector('.avatar, .user-avatar, [class*="avatar"], [class*="Avatar"]');
                    const userName = document.querySelector('.user-name, [class*="userName"], [class*="user-name"]');
                    const logout = document.querySelector('.logout, [class*="logout"], [class*="Logout"]');
                    return {
                        url: window.location.href,
                        title: document.title,
                        hasAvatar: !!avatar,
                        hasUserName: !!userName,
                        hasLogout: !!logout,
                    };
                })()
            """,
        })
        state = json.loads(result.output or "{}")
        log(f"Login state: {json.dumps(state, ensure_ascii=False)}")

        if state.get("hasAvatar") or state.get("hasUserName") or state.get("hasLogout"):
            await explore_workbench(browser, download_dir)
        else:
            log("No logged-in indicators found, but continuing to explore anyway...")
            await explore_workbench(browser, download_dir)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
