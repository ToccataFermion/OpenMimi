"""Find gap by inspecting CAPTCHA overlay elements and rendered styles."""
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


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=["--disable-blink-features=AutomationControlled"],
    )

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
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                    if (btn) { btn.click(); return {clicked: true}; }
                    return {clicked: false};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        # Inspect all elements inside CAPTCHA modal
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                    if (!verify) return {error: 'CAPTCHA not found'};

                    const all = verify.querySelectorAll('*');
                    const info = [];
                    for (const el of all) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        // Look for elements with borders, shadows, or distinctive backgrounds
                        const hasBorder = style.borderWidth !== '0px' && style.borderColor !== 'rgba(0, 0, 0, 0)';
                        const hasShadow = style.boxShadow !== 'none';
                        const hasOutline = style.outlineWidth !== '0px';
                        const isAbsolute = style.position === 'absolute';
                        const zIndex = parseInt(style.zIndex) || 0;

                        if (hasBorder || hasShadow || hasOutline || (isAbsolute && zIndex > 0)) {
                            info.push({
                                tag: el.tagName,
                                class: el.className,
                                id: el.id,
                                rect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height},
                                border: hasBorder ? {width: style.borderWidth, color: style.borderColor, style: style.borderStyle} : null,
                                shadow: hasShadow ? style.boxShadow : null,
                                outline: hasOutline ? {width: style.outlineWidth, color: style.outlineColor} : null,
                                background: style.backgroundColor,
                                position: style.position,
                                zIndex: style.zIndex,
                            });
                        }
                    }

                    // Also check for canvas elements
                    const canvases = verify.querySelectorAll('canvas');

                    return {
                        totalElements: all.length,
                        distinctiveElements: info.length,
                        elements: info,
                        hasCanvas: canvases.length,
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"Overlay analysis: {json.dumps(data, ensure_ascii=False, indent=2)}")

        # Try to find the gap by pixel analysis on a canvas
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                    const bg = document.querySelector('.bottomImage');
                    if (!bg) return {error: 'no bg'};

                    // Create a canvas and draw the background image
                    const canvas = document.createElement('canvas');
                    canvas.width = bg.naturalWidth || bg.width;
                    canvas.height = bg.naturalHeight || bg.height;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(bg, 0, 0);

                    // Get pixel data
                    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                    const data = imageData.data;

                    // Look for distinctive patterns - scan columns for unusual color distributions
                    const w = canvas.width;
                    const h = canvas.height;
                    const colStats = [];
                    for (let x = 0; x < w; x += 5) {
                        let rSum = 0, gSum = 0, bSum = 0, brightCount = 0;
                        for (let y = 0; y < h; y++) {
                            const idx = (y * w + x) * 4;
                            rSum += data[idx];
                            gSum += data[idx + 1];
                            bSum += data[idx + 2];
                            if (data[idx] > 240 && data[idx + 1] > 240 && data[idx + 2] > 240) {
                                brightCount++;
                            }
                        }
                        colStats.push({
                            x,
                            avgR: Math.round(rSum / h),
                            avgG: Math.round(gSum / h),
                            avgB: Math.round(bSum / h),
                            bright: brightCount,
                        });
                    }

                    // Find columns with unusually high brightness (possible gap)
                    const avgBright = colStats.reduce((s, c) => s + c.bright, 0) / colStats.length;
                    const brightCols = colStats.filter(c => c.bright > avgBright * 3 && c.bright > 20);

                    return {
                        width: w,
                        height: h,
                        avgBright: Math.round(avgBright),
                        brightColumns: brightCols.slice(0, 10),
                        columnSamples: colStats.slice(0, 10),
                    };
                })()
            """,
        })
        data2 = json.loads(result.output or "{}")
        log(f"Pixel analysis: {json.dumps(data2, ensure_ascii=False, indent=2)}")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
