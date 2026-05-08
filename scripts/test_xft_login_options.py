"""Inspect xft login page for alternative login methods."""
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
        browser_args=["--disable-blink-features=AutomationControlled"],
    )

    try:
        log("Navigate to xft")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)

        log("Click login tab")
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Inspect login options")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    // Find all tabs/login options
                    const tabs = Array.from(document.querySelectorAll('.ant-tabs-tab, [role="tab"], .login-tab, .tab-item'));
                    const tabInfo = tabs.map(t => ({
                        text: t.textContent?.trim(),
                        className: t.className,
                        ariaSelected: t.getAttribute('aria-selected'),
                    }));

                    // Find QR code login
                    const qr = document.querySelector('.qr-code, .qrcode, [class*="qr"]');
                    const qrInfo = qr ? {
                        className: qr.className,
                        hasImg: !!qr.querySelector('img'),
                    } : null;

                    // Find SMS/code login
                    const smsBtn = Array.from(document.querySelectorAll('button, a, div')).find(el =>
                        /验证码|短信|sms|code/i.test(el.textContent)
                    );

                    // Find any other login links
                    const allLinks = Array.from(document.querySelectorAll('a, button, [class*="login"], [class*="auth"]')).map(el => ({
                        tag: el.tagName,
                        text: el.textContent?.trim()?.slice(0, 50),
                        className: el.className?.slice(0, 100),
                    }));

                    // Check for password-less options
                    const passwordless = Array.from(document.querySelectorAll('*')).filter(el =>
                        /扫码|二维码|微信|企业微信|钉钉|sso/i.test(el.textContent || '')
                    ).map(el => el.textContent?.trim()?.slice(0, 50));

                    return {
                        tabs: tabInfo,
                        qrInfo,
                        smsBtnText: smsBtn?.textContent?.trim(),
                        smsBtnClass: smsBtn?.className,
                        allLinks: allLinks.slice(0, 30),
                        passwordlessHints: [...new Set(passwordless)].slice(0, 10),
                    };
                })()
            """,
        })
        log(f"Login options: {result.output}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
