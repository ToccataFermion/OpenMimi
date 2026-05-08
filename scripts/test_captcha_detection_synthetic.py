"""Synthetic test: inject a fake CAPTCHA element and verify detection."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
    )

    try:
        log("Step 1: Navigate to example.com")
        await browser({"action": "navigate", "url": "https://example.com"})
        await asyncio.sleep(1.0)

        log("Step 2: Take snapshot before injection (should NOT detect CAPTCHA)")
        result = await browser({"action": "snapshot"})
        has_captcha = result.details.get("captcha_detected", False) if result.details else False
        log(f"  CAPTCHA detected: {has_captcha}")
        if has_captcha:
            log("  FAIL: False positive on example.com!")
            return
        log("  PASS: No false positive")

        log("Step 3: Inject fake CAPTCHA element with slider keyword text")
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const div = document.createElement('div');
                    div.className = 'xftImageVerify';
                    div.innerHTML = '<div>请向右滑动滑块完成验证</div>';
                    div.style.position = 'fixed';
                    div.style.top = '100px';
                    div.style.left = '100px';
                    div.style.width = '300px';
                    div.style.height = '200px';
                    div.style.background = 'white';
                    div.style.zIndex = 9999;
                    document.body.appendChild(div);
                    return {injected: true};
                })()
            """,
        })
        await asyncio.sleep(0.5)

        log("Step 4: Take snapshot after injection (should detect CAPTCHA)")
        result = await browser({"action": "snapshot"})
        has_captcha = result.details.get("captcha_detected", False) if result.details else False
        log(f"  CAPTCHA detected: {has_captcha}")
        if not has_captcha:
            log("  FAIL: CAPTCHA not detected after injection!")
            log(f"  Output preview: {(result.output or '')[:500]}")
            return
        log("  PASS: CAPTCHA correctly detected")

        log("Step 5: Remove fake element and verify no detection")
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const el = document.querySelector('.xftImageVerify');
                    if (el) el.remove();
                    return {removed: true};
                })()
            """,
        })
        await asyncio.sleep(0.5)

        result = await browser({"action": "snapshot"})
        has_captcha = result.details.get("captcha_detected", False) if result.details else False
        log(f"  CAPTCHA detected after removal: {has_captcha}")
        if has_captcha:
            log("  FAIL: CAPTCHA still detected after removal!")
            return
        log("  PASS: No detection after removal")

        log("\nAll tests passed!")

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
