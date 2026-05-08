"""xft.cmbchina.com workbench exploration after successful login.

Logs in using the proven DL CAPTCHA solver, then systematically explores
all workbench features by clicking navigation items and capturing screenshots.
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


# Fix Windows console UTF-8 output so Chinese text renders correctly
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


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
    phone_num = os.environ.get('XFT_PHONE', '')
    password = os.environ.get('XFT_PASSWORD', '')
    if not phone_num or not password:
        log("ERROR: XFT_PHONE and XFT_PASSWORD environment variables must be set")
        return False
    phone_result = await browser({
        "action": "react_fill",
        "ref": "@input.ant-input[type='text']",
        "value": phone_num,
    })
    pass_result = await browser({
        "action": "react_fill",
        "ref": "@input.ant-input[type='password']",
        "value": password,
    })
    checkbox_result = await browser({
        "action": "eval",
        "js": "(() => { const cb = document.querySelector('input.ant-checkbox-input'); if (cb && !cb.checked) cb.click(); return {checked: !!cb && cb.checked}; })()",
    })
    has_phone = not phone_result.is_error
    has_pass = not pass_result.is_error
    log(f"Form fill: phone={has_phone}, pass={has_pass}, checkbox={checkbox_result.output}")
    return has_phone and has_pass


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


async def get_gap_dl(browser: AgentBrowserTool, download_dir: str) -> int | None:
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
        crop_path = screenshot_path
    else:
        left = modal_data.get("left", 0)
        top = modal_data.get("top", 0)
        width = modal_data.get("width", 0)
        height = modal_data.get("height", 0)
        log(f"Modal: left={left}, top={top}, width={width}, height={height}")
        try:
            from PIL import Image
            img = Image.open(screenshot_path)
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

    gap = await get_gap_dl(browser, download_dir)
    if gap is not None and gap > 10:
        handle_drag = int(gap * 280 / 262)
        log(f"DL suggests handle_drag={handle_drag}px")
        for offset in [0, 10, -10, 20, -20]:
            if await try_drag(browser, computer, sx, sy, handle_drag + offset):
                log("SUCCESS with DL!")
                return True
            await asyncio.sleep(1.0)

    log("Brute force fallback...")
    for dist in range(80, 261, 15):
        if await try_drag(browser, computer, sx, sy, dist):
            log(f"SUCCESS at {dist}px!")
            return True
        await asyncio.sleep(2.0)

    return False


async def capture_auth_state(browser: AgentBrowserTool, download_dir: str) -> None:
    log("\n=== Capturing Auth State ===")
    result = await browser({
        "action": "storage",
        "storage_action": "get",
        "storage_type": "cookies",
    })
    cookies_path = os.path.join(download_dir, "auth_cookies.json")
    with open(cookies_path, "w", encoding="utf-8") as f:
        f.write(result.output or "{}")
    log(f"Cookies saved to {cookies_path}")

    result = await browser({
        "action": "storage",
        "storage_action": "get",
        "storage_type": "localStorage",
    })
    ls_path = os.path.join(download_dir, "auth_localStorage.json")
    with open(ls_path, "w", encoding="utf-8") as f:
        f.write(result.output or "{}")
    log(f"localStorage saved to {ls_path}")

    url_result = await browser({"action": "get_url"})
    title_result = await browser({"action": "get_title"})
    result = await browser({
        "action": "eval",
        "js": "(() => ({userAgent: navigator.userAgent, token: localStorage.getItem('token') || localStorage.getItem('accessToken') || sessionStorage.getItem('token') || null}))()",
    })
    auth_data = json.loads(result.output or "{}")
    auth_data["url"] = url_result.output
    auth_data["title"] = title_result.output
    state_path = os.path.join(download_dir, "auth_state.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(auth_data, f, ensure_ascii=False, indent=2)
    log(f"Auth state saved to {state_path}")


async def explore_workbench(browser: AgentBrowserTool, download_dir: str) -> None:
    log("\n=== Exploring Workbench ===")

    await browser({"action": "screenshot", "path": os.path.join(download_dir, "workbench_overview.png"), "annotate": True})

    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const items = [];
                const selectors = [
                    '.ant-menu-item',
                    '.ant-menu-submenu-title',
                    '.ant-layout-sider a',
                    '.ant-layout-header a',
                    '.nav-item',
                    '.menu-item',
                    '[class*="menu"] > li',
                    '[class*="nav"] > a',
                    '[class*="nav"] > li',
                    '.sidebar a',
                    '.sider a',
                ];
                for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                        const text = (el.innerText || el.textContent || '').trim();
                        if (text && text.length > 0 && text.length < 50) {
                            const rect = el.getBoundingClientRect();
                            items.push({
                                selector: sel,
                                text: text.substring(0, 40),
                                tag: el.tagName,
                                className: el.className,
                                href: el.href || null,
                                x: Math.round(rect.left + rect.width/2),
                                y: Math.round(rect.top + rect.height/2),
                            });
                        }
                    }
                }
                const seen = new Set();
                return items.filter(i => { if (seen.has(i.text)) return false; seen.add(i.text); return true; });
            })()
        """,
    })
    nav_items = json.loads(result.output or "[]")
    log(f"Found {len(nav_items)} unique navigation items")
    for i, item in enumerate(nav_items):
        log(f"  [{i}] {item.get('text')} ({item.get('selector')})")

    log("\n=== Starting Network Log Capture ===")
    await browser({"action": "network_log", "duration_ms": 3000, "filter": "cmbchina"})

    explored = 0
    for item in nav_items[:8]:
        text = item.get("text", "")
        if not text or text in ("登录", "注册", "退出", "登出"):
            continue
        log(f"\n--- Clicking: {text} ---")
        try:
            await browser({"action": "click", "target_text": text})
            await asyncio.sleep(2.5)
            await browser({
                "action": "screenshot",
                "path": os.path.join(download_dir, f"workbench_{explored:02d}_{text.replace(' ', '_').replace('/', '_')[:20]}.png"),
            })
            url_r = await browser({"action": "get_url"})
            title_r = await browser({"action": "get_title"})
            log(f"  -> {title_r.output} | {url_r.output}")
            explored += 1
        except Exception as exc:
            log(f"  ERROR clicking {text}: {exc}")

    await browser({"action": "screenshot", "path": os.path.join(download_dir, "workbench_final.png")})
    log(f"\nExplored {explored} sections. Screenshots saved to {download_dir}")


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
            if await solve_captcha(browser, computer, download_dir):
                log("=== LOGIN SUCCESS ===")
            else:
                log("=== LOGIN FAILED (CAPTCHA) ===")
                return
        else:
            log("=== No CAPTCHA - may be logged in ===")

        url_r = await browser({"action": "get_url"})
        title_r = await browser({"action": "get_title"})
        result = await browser({
            "action": "eval",
            "js": "(() => ({hasAvatar: !!document.querySelector('.avatar, .user-avatar, [class*=\\\"avatar\\\"], [class*=\\\"Avatar\\\"]'), hasUserName: !!document.querySelector('.user-name, [class*=\\\"userName\\\"], [class*=\\\"user-name\\\"]'), hasLogout: !!document.querySelector('.logout, [class*=\\\"logout\\\"], [class*=\\\"Logout\\\"]')}))()",
        })
        state = json.loads(result.output or "{}")
        state["url"] = url_r.output
        state["title"] = title_r.output
        log(f"Login state: {json.dumps(state, ensure_ascii=False)}")

        if state.get("hasAvatar") or state.get("hasUserName") or state.get("hasLogout"):
            await capture_auth_state(browser, download_dir)
            await explore_workbench(browser, download_dir)
        else:
            log("No logged-in indicators found, but continuing to explore anyway...")
            await capture_auth_state(browser, download_dir)
            await explore_workbench(browser, download_dir)

    finally:
        await browser.close()
        log(f"Browser closed. Download dir: {download_dir}")


if __name__ == "__main__":
    asyncio.run(main())
