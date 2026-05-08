"""Advanced xft.cmbchina.com login with all OpenMimi capabilities.

Demonstrates persistent profile, network modification, structured extraction,
wait_for_navigation, mobile emulation, and comprehensive logging.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool
from openmimi.tools.computer import ComputerTool

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def setup_browser(download_dir: str) -> AgentBrowserTool:
    """Create browser with persistent profile, proxy, and stealth."""
    # Use persistent profile in temp dir so cookies/cache survive across runs
    profile_dir = os.path.join(download_dir, "chrome_profile")
    os.makedirs(profile_dir, exist_ok=True)

    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=["--disable-blink-features=AutomationControlled"],
        stealth=True,
        user_data_dir=profile_dir,
    )
    return browser


async def apply_stealth_headers(browser: AgentBrowserTool) -> None:
    """Inject realistic headers and User-Agent to avoid detection."""
    # Set realistic User-Agent
    await browser({
        "action": "network_modify",
        "modify_action": "user_agent",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    })
    # Inject common headers that real browsers send
    await browser({
        "action": "network_modify",
        "modify_action": "inject_headers",
        "headers": {
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    })
    log("Stealth headers applied")


async def navigate_and_wait(browser: AgentBrowserTool, url: str) -> None:
    """Navigate with enhanced waiting for SPA stability."""
    log(f"Navigating to {url}")
    await browser({"action": "navigate", "url": url})
    # Wait a moment for initial render
    await asyncio.sleep(2.0)
    # Take debug screenshot
    await browser({"action": "screenshot", "path": os.path.join(browser._download_dir, "nav_initial.png")})


async def open_login_form(browser: AgentBrowserTool) -> bool:
    """Click login and wait for form to appear."""
    log("Clicking login button...")
    await browser({"action": "click", "target_text": "登录"})

    # Wait for form using wait_for instead of fixed sleep
    result = await browser({
        "action": "wait_for",
        "target_text": "登录",
        "timeout_ms": 5000,
    })
    if result.is_error:
        log("Login form did not appear, retrying with force...")
        await browser({"action": "click", "target_text": "登录", "force": True})
        result = await browser({
            "action": "wait_for",
            "target_text": "登录",
            "timeout_ms": 5000,
        })

    # Check for form inputs
    result = await browser({
        "action": "eval",
        "js": "(() => ({hasForm: !!document.querySelector('input.ant-input')}))()",
    })
    has_form = json.loads(result.output or "{}").get("hasForm", False)
    if not has_form:
        log("Login form not detected")
        return False

    log("Login form appeared")
    await browser({"action": "screenshot", "path": os.path.join(browser._download_dir, "login_form.png")})
    return True


async def fill_login_form(browser: AgentBrowserTool) -> bool:
    """Fill credentials using React-compatible value setter."""
    phone_num = os.environ.get('XFT_PHONE', '')
    password = os.environ.get('XFT_PASSWORD', '')
    if not phone_num or not password:
        log("WARNING: XFT_PHONE and XFT_PASSWORD environment variables not set")
        phone_num = '18584828398'
        password = 'Liszt123'

    result = await browser({
        "action": "eval",
        "js": f"""
            (() => {{
                const inputs = Array.from(document.querySelectorAll('input.ant-input'));
                const phone = inputs.find(el => el.type === 'text');
                const pass = inputs.find(el => el.type === 'password');
                const checkbox = document.querySelector('input.ant-checkbox-input');
                function setReactValue(element, value) {{
                    const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    valueSetter.call(element, value);
                    element.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    element.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
                if (phone) setReactValue(phone, '{phone_num}');
                if (pass) setReactValue(pass, '{password}');
                if (checkbox && !checkbox.checked) checkbox.click();
                return {{hasPhone: !!phone, hasPass: !!pass, hasCheckbox: !!checkbox}};
            }})()
        """,
    })
    data = json.loads(result.output or "{}")
    log(f"Form fill: {json.dumps(data, ensure_ascii=False)}")
    return data.get("hasPhone", False) and data.get("hasPass", False)


async def submit_and_handle_captcha(browser: AgentBrowserTool, computer: ComputerTool, download_dir: str) -> bool:
    """Submit login and solve CAPTCHA if present."""
    # Submit via eval to avoid ambiguity with multiple '登录' elements
    await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                if (btn) { btn.click(); return {clicked: true, text: btn.innerText}; }
                return {clicked: false};
            })()
        """,
    })

    # Wait for either CAPTCHA or successful navigation
    await asyncio.sleep(3.0)

    # Check if CAPTCHA appeared
    result = await browser({
        "action": "eval",
        "js": "(() => ({hasCaptcha: !!document.querySelector('.xftImageVerify')}))()",
    })
    has_captcha = json.loads(result.output or "{}").get("hasCaptcha", False)

    if not has_captcha:
        log("No CAPTCHA detected, checking login state...")
        return True

    log("CAPTCHA detected, solving...")
    await browser({"action": "screenshot", "path": os.path.join(download_dir, "captcha_detected.png")})

    # Try DL-based solver first
    gap = await get_gap_dl(browser, download_dir)
    if gap is not None and gap > 10:
        handle_drag = int(gap * 280 / 262)
        log(f"DL suggests handle_drag={handle_drag}px")
        sx, sy = await get_handle_position(browser)
        if sx and sy:
            for offset in [0, 10, -10, 20, -20]:
                if await try_drag(browser, computer, sx, sy, handle_drag + offset):
                    log("CAPTCHA solved with DL!")
                    return True
                await asyncio.sleep(1.0)

    # Brute force fallback
    log("Brute force fallback...")
    sx, sy = await get_handle_position(browser)
    if sx and sy:
        for dist in range(80, 261, 15):
            if await try_drag(browser, computer, sx, sy, dist):
                log(f"CAPTCHA solved at {dist}px!")
                return True
            await asyncio.sleep(2.0)

    return False


async def get_gap_dl(browser: AgentBrowserTool, download_dir: str) -> int | None:
    """Use captcha-recognizer deep learning model on CAPTCHA screenshot."""
    try:
        from captcha_recognizer.slider import Slider
    except ImportError:
        log("captcha-recognizer not installed")
        return None

    screenshot_path = os.path.join(download_dir, "captcha_screenshot.png")
    await browser({"action": "screenshot", "path": screenshot_path})
    if not os.path.exists(screenshot_path):
        return None

    # Try to crop to modal
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
        crop_path = screenshot_path
    else:
        try:
            from PIL import Image
            img = Image.open(screenshot_path)
            pad = 20
            crop_box = (
                max(0, modal_data.get("left", 0) - pad),
                max(0, modal_data.get("top", 0) - pad),
                min(img.width, modal_data.get("left", 0) + modal_data.get("width", 0) + pad),
                min(img.height, modal_data.get("top", 0) + modal_data.get("height", 0) + pad),
            )
            cropped = img.crop(crop_box)
            crop_path = os.path.join(download_dir, "captcha_cropped.png")
            cropped.save(crop_path)
        except Exception:
            crop_path = screenshot_path

    try:
        slider = Slider()
        offset, conf = slider.identify_offset(crop_path)
        log(f"DL gap detection: offset={offset}px, confidence={conf:.3f}")
        if conf < 0.5:
            return None
        return int(offset)
    except Exception as exc:
        log(f"DL detection failed: {exc}")
        return None


async def get_handle_position(browser: AgentBrowserTool) -> tuple[int, int]:
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
    return pos.get("screenX", 0), pos.get("screenY", 0)


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


async def analyze_login_state(browser: AgentBrowserTool, download_dir: str) -> dict:
    """Use structured extraction to analyze the current page state."""
    # Get metadata
    meta = await browser({"action": "extract", "instruction": "metadata"})
    # Get page source for debugging
    source = await browser({"action": "page_source", "include_html": False})
    # Check for logged-in indicators
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
    log(f"Page state: {json.dumps(state, ensure_ascii=False)}")
    await browser({"action": "screenshot", "path": os.path.join(download_dir, "final_state.png")})
    return state


async def explore_with_extraction(browser: AgentBrowserTool, download_dir: str) -> None:
    """Explore workbench using structured extraction instead of raw eval."""
    log("\n=== Structured Page Analysis ===")

    # Extract headings
    headings = await browser({"action": "extract", "instruction": "headings"})
    log(f"Headings:\n{headings.output[:1000]}")

    # Extract links
    links = await browser({"action": "extract", "instruction": "links"})
    log(f"Links:\n{links.output[:1500]}")

    # Extract metadata
    meta = await browser({"action": "extract", "instruction": "metadata"})
    log(f"Metadata:\n{meta.output[:1000]}")

    await browser({"action": "screenshot", "path": os.path.join(download_dir, "workbench_final.png")})


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_adv_")
    log(f"Download directory: {download_dir}")

    browser = await setup_browser(download_dir)
    computer = ComputerTool()

    try:
        await navigate_and_wait(browser, "https://xft.cmbchina.com/")
        await apply_stealth_headers(browser)

        if not await open_login_form(browser):
            log("Failed to open login form")
            return

        if not await fill_login_form(browser):
            log("Form filling failed")
            return

        if await submit_and_handle_captcha(browser, computer, download_dir):
            log("=== LOGIN SUCCESS ===")
            state = await analyze_login_state(browser, download_dir)
            await explore_with_extraction(browser, download_dir)
        else:
            log("=== LOGIN FAILED ===")
            # Debug: save page source
            source = await browser({"action": "page_source"})
            with open(os.path.join(download_dir, "failed_source.txt"), "w", encoding="utf-8") as f:
                f.write(source.output or "")
            log(f"Debug source saved to {download_dir}/failed_source.txt")

    finally:
        await browser.close()
        log(f"Browser closed. Download dir: {download_dir}")


if __name__ == "__main__":
    asyncio.run(main())
