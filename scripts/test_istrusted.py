"""Verify whether ComputerTool SendInput generates trusted events in Chrome."""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool
from openmimi.tools.computer import ComputerTool

HTML_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_istrusted.html"))

async def main():
    import tempfile
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser = AgentBrowserTool(download_dir=download_dir, headless=False, viewport=(1280, 800))
    computer = ComputerTool()
    try:
        await browser({"action": "navigate", "url": f"file:///{HTML_PATH}"})
        await asyncio.sleep(1.0)

        # Get box position
        result = await browser({"action": "eval", "js": "(() => { const r = document.getElementById('box').getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height, sx: window.screenX, sy: window.screenY, ow: window.outerWidth, oh: window.outerHeight, iw: window.innerWidth, ih: window.innerHeight}; })()"})
        data = json.loads(result.output or "{}")
        print(f"Box: {data}")

        left_frame = (data["ow"] - data["iw"]) // 2
        top_frame = data["oh"] - data["ih"] - left_frame
        cx = int(data["sx"] + left_frame + data["x"] + data["w"] / 2)
        cy = int(data["sy"] + top_frame + data["y"] + data["h"] / 2)
        print(f"Screen center: ({cx}, {cy})")

        # Focus browser window first
        try:
            import win32gui, win32con
            def callback(hwnd, extra):
                if win32gui.IsWindowVisible(hwnd) and "Chrome" in win32gui.GetWindowText(hwnd):
                    extra.append(hwnd)
                return True
            handles = []
            win32gui.EnumWindows(callback, handles)
            if handles:
                hwnd = handles[-1]
                win32gui.SetForegroundWindow(hwnd)
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                await asyncio.sleep(0.5)
                print(f"Focused window: {win32gui.GetWindowText(hwnd)}")
        except Exception as e:
            print(f"Focus error: {e}")

        # Click via ComputerTool
        await computer({"action": "mouse_click", "x": cx, "y": cy})
        await asyncio.sleep(0.5)

        # Drag via ComputerTool
        await computer({"action": "mouse_drag", "x": cx - 50, "y": cy, "end_x": cx + 50, "end_y": cy, "steps": 20})
        await asyncio.sleep(0.5)

        # Read log
        result = await browser({"action": "eval", "js": "document.getElementById('log').textContent"})
        print("Event log:")
        print(result.output)
    finally:
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
