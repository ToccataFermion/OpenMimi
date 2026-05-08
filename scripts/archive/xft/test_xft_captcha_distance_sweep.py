"""Try different drag distances to find the correct one for xft CAPTCHA."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def do_login(tool: AgentBrowserTool) -> None:
    log("  Navigate")
    await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
    log("  Click login")
    await tool({"action": "click", "target_text": "登录"})
    await asyncio.sleep(2.0)
    log("  Fill credentials")
    await tool({
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
    log("  Click login button")
    await tool({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('div[class*="PasswordLogin_loginBtn"]');
                if (btn) {
                    btn.focus();
                    ['mousedown', 'mouseup', 'click'].forEach(type => {
                        btn.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                    });
                }
                return {clicked: !!btn};
            })()
        """,
    })
    await asyncio.sleep(2.0)


async def perform_drag(tool: AgentBrowserTool, start_x: int, start_y: int, distance: int) -> None:
    log(f"  Drag by {distance}px")
    await tool._exec("mouse", "move", str(start_x), str(start_y), "--json")
    await asyncio.sleep(0.2)
    await tool._exec("mouse", "down", "--json")
    await asyncio.sleep(0.2)

    steps = 25
    for i in range(1, steps + 1):
        t = i / steps
        ease = 1 - (1 - t) ** 3
        cx = start_x + int(distance * ease)
        cy = start_y + (i % 3 - 1)
        await tool._exec("mouse", "move", str(cx), str(cy), "--json")
        await asyncio.sleep(0.03 + (i % 5) * 0.005)

    await tool._exec("mouse", "up", "--json")
    await asyncio.sleep(0.3)


async def check_result(tool: AgentBrowserTool) -> dict:
    result = await tool({"action": "snapshot"})
    text = result.output or ""
    return {
        "has_slider": "滑块" in text,
        "has_puzzle": "拼图" in text,
        "has_fail": "验证失败" in text,
        "has_success": "验证成功" in text,
        "has_workbench": "工作台" in text,
        "has_password_login": "密码登录" in text,
    }


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-web-security",
        "--disable-features=IsolateOrigins,site-per-process",
    ]
    tool = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        browser_args=browser_args,
    )

    # Try several distances
    # Based on gap detection: gap is at ~665 viewport, drag starts at 459
    # So dragImage needs to move ~206px (left edge alignment)
    # But button-to-drag ratio is ~0.93, so button needs ~222px
    # Also try: 167 (gap center), 128 (gap right edge theory), 222 (gap left edge theory)
    distances_to_try = [128, 167, 180, 206, 222, 240]

    try:
        for distance in distances_to_try:
            log(f"\n=== Trying distance: {distance}px ===")
            await do_login(tool)

            # Get button position
            result = await tool({
                "action": "eval",
                "js": """
                    (() => {
                        const btn = document.querySelector('.imageVerifyDragButton');
                        if (!btn) return {error: 'no button'};
                        const r = btn.getBoundingClientRect();
                        return {x: r.x, y: r.y, w: r.width, h: r.height};
                    })()
                """,
            })
            btn_rect = json.loads(result.output or "{}").get("btnRect")
            if not btn_rect:
                log("  No CAPTCHA button, skipping")
                continue

            start_x = int(btn_rect["x"] + btn_rect["w"] / 2)
            start_y = int(btn_rect["y"] + btn_rect["h"] / 2)
            await perform_drag(tool, start_x, start_y, distance)

            await asyncio.sleep(3.0)
            result = await check_result(tool)
            log(f"  Result: {result}")

            if result["has_workbench"] or result["has_success"]:
                log(f"  SUCCESS with distance={distance}!")
                break
            if not result["has_slider"] and not result["has_puzzle"] and not result["has_password_login"]:
                log(f"  CAPTCHA/modal disappeared with distance={distance}")
                break

            # Small delay before next attempt
            await asyncio.sleep(2.0)

        log("\nDone. Keeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
