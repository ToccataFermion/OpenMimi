"""Try to find gap by comparing puzzle piece against background at each offset."""
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

        # Pixel-level comparison using canvas
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
                    bgCanvas.width = bgW;
                    bgCanvas.height = bgH;
                    const bgCtx = bgCanvas.getContext('2d');
                    bgCtx.drawImage(bg, 0, 0);
                    const bgData = bgCtx.getImageData(0, 0, bgW, bgH).data;

                    const pCanvas = document.createElement('canvas');
                    pCanvas.width = pW;
                    pCanvas.height = pH;
                    const pCtx = pCanvas.getContext('2d');
                    pCtx.drawImage(piece, 0, 0);
                    const pData = pCtx.getImageData(0, 0, pW, pH).data;

                    // Compare piece against bg at each possible offset
                    const maxOffset = bgW - pW;
                    const scores = [];

                    for (let ox = 0; ox <= maxOffset; ox++) {
                        let diff = 0;
                        let validPixels = 0;
                        for (let py = 0; py < pH; py += 2) {
                            for (let px = 0; px < pW; px += 2) {
                                const pIdx = (py * pW + px) * 4;
                                const bgIdx = (py * bgW + (ox + px)) * 4;

                                // Skip transparent pixels in piece
                                if (pData[pIdx + 3] < 128) continue;

                                const dr = Math.abs(pData[pIdx] - bgData[bgIdx]);
                                const dg = Math.abs(pData[pIdx + 1] - bgData[bgIdx + 1]);
                                const db = Math.abs(pData[pIdx + 2] - bgData[bgIdx + 2]);
                                diff += dr + dg + db;
                                validPixels++;
                            }
                        }
                        scores.push({
                            offset: ox,
                            diff: validPixels > 0 ? Math.round(diff / validPixels) : 99999,
                            validPixels
                        });
                    }

                    // Find min and max diff positions
                    const sorted = scores.slice().sort((a, b) => a.diff - b.diff);
                    const minDiff = sorted.slice(0, 5);
                    const maxDiff = sorted.slice(-5).reverse();

                    return {
                        bgW, bgH, pW, pH, maxOffset,
                        minDiff,
                        maxDiff,
                        allScores: scores.filter((_, i) => i % 10 === 0)
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"Pixel diff analysis: {json.dumps(data, ensure_ascii=False, indent=2)}")

        if data.get("maxDiff"):
            best = data["maxDiff"][0]  # Highest difference = likely gap
            gap_px = best["offset"]
            log(f"\nEstimated gap (max diff): {gap_px}px")

            # Get handle pos and drag
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

            handle_drag = int(gap_px * 280 / 262)
            log(f"Handle drag: {handle_drag}px")

            await browser({"action": "focus"})
            await asyncio.sleep(0.3)
            await computer({
                "action": "mouse_drag",
                "x": sx, "y": sy,
                "end_x": sx + handle_drag, "end_y": sy,
                "steps": 80, "delay_ms": 25,
            })
            await asyncio.sleep(2.0)

            result = await browser({
                "action": "eval",
                "js": """
                    (() => {
                        const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                        return {hasVerify: !!verify};
                    })()
                """,
            })
            state = json.loads(result.output or "{}")
            if not state.get("hasVerify"):
                log("SUCCESS!")
            else:
                log("Failed. Trying nearby offsets...")
                for offset in [-10, 10, -20, 20]:
                    try_drag = handle_drag + offset
                    if try_drag < 0 or try_drag > 280:
                        continue
                    await browser({"action": "focus"})
                    await asyncio.sleep(0.3)
                    await computer({
                        "action": "mouse_drag",
                        "x": sx, "y": sy,
                        "end_x": sx + try_drag, "end_y": sy,
                        "steps": 80, "delay_ms": 25,
                    })
                    await asyncio.sleep(2.0)
                    result = await browser({
                        "action": "eval",
                        "js": "(() => ({hasVerify: !!document.querySelector('.xftImageVerify')}))()",
                    })
                    if not json.loads(result.output or "{}").get("hasVerify"):
                        log(f"SUCCESS at offset {offset}!")
                        break
                    await asyncio.sleep(1.0)

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
