"""Debug script v3: test slow drag and inspect CAPTCHA event handling."""
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


async def inspect_state(browser: AgentBrowserTool, label: str) -> dict:
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.imageVerifyDragButton');
                const drag = document.querySelector('.dragImage');
                const track = document.querySelector('.imageVerifyDrag');
                const btnStyle = btn ? window.getComputedStyle(btn) : null;
                const dragStyle = drag ? window.getComputedStyle(drag) : null;
                const trackStyle = track ? window.getComputedStyle(track) : null;
                return {
                    btn: btn ? {
                        left: btnStyle?.left, transform: btnStyle?.transform,
                        rect: {left: btn.getBoundingClientRect().left, top: btn.getBoundingClientRect().top,
                               width: btn.getBoundingClientRect().width, height: btn.getBoundingClientRect().height}
                    } : null,
                    drag: drag ? {
                        left: dragStyle?.left, transform: dragStyle?.transform,
                        rect: {left: drag.getBoundingClientRect().left, top: drag.getBoundingClientRect().top,
                               width: drag.getBoundingClientRect().width, height: drag.getBoundingClientRect().height}
                    } : null,
                    track: track ? {
                        rect: {left: track.getBoundingClientRect().left, top: track.getBoundingClientRect().top,
                               width: track.getBoundingClientRect().width, height: track.getBoundingClientRect().height}
                    } : null,
                };
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    log(f"  [{label}] {json.dumps(data, ensure_ascii=False)}")
    return data


async def slow_drag(computer: ComputerTool, browser: AgentBrowserTool,
                    label: str, sx: int, sy: int, ex: int, ey: int,
                    steps: int = 100, delay_ms: float = 20) -> None:
    log(f"\n--- {label}: slow drag ({sx},{sy}) -> ({ex},{ey}) steps={steps} delay={delay_ms}ms ---")
    # Move to start
    await computer({"action": "mouse_move", "x": sx, "y": sy})
    await asyncio.sleep(0.3)
    # Mouse down
    await computer({"action": "mouse_down", "button": "left"})
    await asyncio.sleep(0.2)
    # Linear interpolation (no bezier, no jitter)
    for i in range(1, steps + 1):
        t = i / steps
        x = int(sx + (ex - sx) * t)
        y = int(sy + (ey - sy) * t)
        await computer({"action": "mouse_move", "x": x, "y": y})
        await asyncio.sleep(delay_ms / 1000)
    await asyncio.sleep(0.2)
    # Mouse up
    await computer({"action": "mouse_up", "button": "left"})
    await asyncio.sleep(0.5)
    await inspect_state(browser, label)


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
        log("=== Login ===")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)
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
        # Click actual login button
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                    if (btn) { btn.click(); return {clicked: true, class: btn.className}; }
                    return {clicked: false};
                })()
            """,
        })
        log(f"  Login click: {result.output}")
        await asyncio.sleep(4.0)

        # Get positions
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const els = {};
                    for (const sel of ['.imageVerifyDragButton', '.dragImage', '.imageVerifyDrag', '.bottomImage']) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const r = el.getBoundingClientRect();
                            els[sel] = {left: r.left, top: r.top, width: r.width, height: r.height, cx: r.left+r.width/2, cy: r.top+r.height/2};
                        }
                    }
                    const sx = window.screenX + (window.outerWidth - window.innerWidth) / 2;
                    const sy = window.screenY + outerHeight - innerHeight - (outerWidth - innerWidth) / 2;
                    return {els, sx, sy, dpr: window.devicePixelRatio};
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        sx = data.get("sx", 0)
        sy = data.get("sy", 0)
        log(f"  Frame offset: ({sx}, {sy})")
        positions = {}
        for sel, info in data.get("els", {}).items():
            screen_x = int(sx + info["cx"])
            screen_y = int(sy + info["cy"])
            positions[sel] = (screen_x, screen_y)
            log(f"  {sel}: viewport=({info['cx']:.1f}, {info['cy']:.1f}), screen=({screen_x}, {screen_y})")

        await browser({"action": "focus"})
        await asyncio.sleep(0.5)

        # Baseline state
        await inspect_state(browser, "baseline")

        # Test 1: Very slow drag on handle
        if ".imageVerifyDragButton" in positions:
            x, y = positions[".imageVerifyDragButton"]
            await slow_drag(computer, browser, "Slow handle drag", x, y, x + 150, y, steps=80, delay_ms=25)

        # Reset by clicking somewhere else
        log("\n--- Reset: click away ---")
        await computer({"action": "mouse_move", "x": 100, "y": 100})
        await asyncio.sleep(0.2)
        await computer({"action": "mouse_click", "x": 100, "y": 100})
        await asyncio.sleep(1.0)
        await inspect_state(browser, "after reset")

        # Test 2: Try using agent_browser CDP drag action
        log("\n--- CDP drag via agent_browser ---")
        if ".imageVerifyDragButton" in positions:
            x, y = positions[".imageVerifyDragButton"]
            # Use browser tool's mouse actions (CDP-level)
            result = await browser({
                "action": "eval",
                "js": f"""
                    (() => {{
                        const btn = document.querySelector('.imageVerifyDragButton');
                        const rect = btn.getBoundingClientRect();
                        return {{cx: rect.left + rect.width/2, cy: rect.top + rect.height/2}};
                    }})()
                """,
            })
            pos = json.loads(result.output or "{{}}")
            cx, cy = int(pos.get("cx", 0)), int(pos.get("cy", 0))
            # CDP mouse action via eval
            await browser({
                "action": "eval",
                "js": f"""
                    (() => {{
                        const btn = document.querySelector('.imageVerifyDragButton');
                        const rect = btn.getBoundingClientRect();
                        const cx = rect.left + rect.width/2;
                        const cy = rect.top + rect.height/2;
                        const events = ['mousedown', 'mousemove', 'mouseup'];
                        const down = new MouseEvent('mousedown', {{
                            bubbles: true, cancelable: true, view: window,
                            clientX: cx, clientY: cy, screenX: cx, screenY: cy,
                            button: 0, buttons: 1
                        }});
                        btn.dispatchEvent(down);
                        // Move in small increments
                        for (let i = 1; i <= 20; i++) {{
                            const nx = cx + i * 7.5;
                            const move = new MouseEvent('mousemove', {{
                                bubbles: true, cancelable: true, view: window,
                                clientX: nx, clientY: cy, screenX: nx, screenY: cy,
                                button: 0, buttons: 1
                            }});
                            document.dispatchEvent(move);
                        }}
                        const up = new MouseEvent('mouseup', {{
                            bubbles: true, cancelable: true, view: window,
                            clientX: cx + 150, clientY: cy, screenX: cx + 150, screenY: cy,
                            button: 0, buttons: 0
                        }});
                        document.dispatchEvent(up);
                        return {{simulated: true}};
                    }})()
                """,
            })
            await asyncio.sleep(0.5)
            await inspect_state(browser, "after CDP eval drag")

        # Test 3: Check if CAPTCHA uses touch events
        log("\n--- Check for touch event listeners ---")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    const track = document.querySelector('.imageVerifyDrag');
                    // Simple check: look for ontouchstart / ontouchmove properties
                    return {
                        btnTouchStart: btn ? typeof btn.ontouchstart : 'no-btn',
                        btnTouchMove: btn ? typeof btn.ontouchmove : 'no-btn',
                        dragTouchStart: drag ? typeof drag.ontouchstart : 'no-drag',
                        dragTouchMove: drag ? typeof drag.ontouchmove : 'no-drag',
                        trackTouchStart: track ? typeof track.ontouchstart : 'no-track',
                        trackTouchMove: track ? typeof track.ontouchmove : 'no-track',
                        hasTouchEvent: 'ontouchstart' in window,
                    };
                })()
            """,
        })
        log(f"  Touch check: {result.output}")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
