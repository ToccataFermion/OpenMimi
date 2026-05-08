"""Edge-based CAPTCHA gap detection.

Instead of comparing all non-transparent pixels (which is confused by
anti-aliased edges and background texture), this script:
1. Builds an edge mask from the puzzle piece alpha channel
2. Slides the edge mask across the background
3. Measures color discontinuity ONLY along the edges
4. Finds the offset with the strongest edge mismatch = gap position
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


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def robust_login(browser: AgentBrowserTool) -> bool:
    """Login with retry logic. Returns True if CAPTCHA appeared."""
    log("Navigating...")
    await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
    await asyncio.sleep(3.0)

    log("Clicking login...")
    await browser({"action": "click", "target_text": "登录"})
    await asyncio.sleep(3.0)

    log("Filling form...")
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
                return {
                    ok: true,
                    hasPhone: !!phone,
                    hasPass: !!pass,
                    hasCheckbox: !!checkbox,
                    url: window.location.href
                };
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    log(f"Form fill: {json.dumps(data)}")
    if not data.get("hasPhone") or not data.get("hasPass"):
        log("Form elements not found, retrying on t2...")
        await browser({"action": "tab_switch", "tab_index": 2})
        await asyncio.sleep(2.0)
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
                    return {
                        ok: true,
                        hasPhone: !!phone,
                        hasPass: !!pass,
                        url: window.location.href
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"Form fill retry: {json.dumps(data)}")

    await asyncio.sleep(0.5)

    log("Clicking login button...")
    result = await browser({
        "action": "eval",
        "js": """
            (() => {
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                if (btn) { btn.click(); return {clicked: true, class: btn.className}; }
                const alt = document.querySelector('button[type="submit"]');
                if (alt) { alt.click(); return {clicked: true, class: alt.className, alt: true}; }
                return {clicked: false, html: document.body.innerHTML.substring(0, 200)};
            })()
        """,
    })
    log(f"Login click: {result.output}")
    await asyncio.sleep(4.0)

    result = await browser({
        "action": "eval",
        "js": "(() => ({hasCaptcha: !!document.querySelector('.xftImageVerify')}))()",
    })
    has_captcha = json.loads(result.output or "{}").get("hasCaptcha", False)
    log(f"CAPTCHA present: {has_captcha}")
    return has_captcha


async def get_gap_by_edge(browser: AgentBrowserTool) -> int | None:
    """Use edge-based pixel comparison to estimate gap."""
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
                bgCanvas.width = bgW; bgCanvas.height = bgH;
                bgCanvas.getContext('2d').drawImage(bg, 0, 0);
                const bgData = bgCanvas.getContext('2d').getImageData(0, 0, bgW, bgH).data;

                const pCanvas = document.createElement('canvas');
                pCanvas.width = pW; pCanvas.height = pH;
                pCanvas.getContext('2d').drawImage(piece, 0, 0);
                const pData = pCanvas.getContext('2d').getImageData(0, 0, pW, pH).data;

                // Build edge mask: pixels where alpha transitions from opaque to transparent
                const edgePixels = [];
                for (let py = 0; py < pH; py += 2) {
                    for (let px = 0; px < pW; px += 2) {
                        const idx = (py * pW + px) * 4;
                        const alpha = pData[idx + 3];
                        // Look for boundary pixels (edge of opaque region)
                        if (alpha > 128) {
                            let isEdge = false;
                            // Check 4-connected neighbors
                            const neighbors = [[-1,0],[1,0],[0,-1],[0,1]];
                            for (const [dx, dy] of neighbors) {
                                const nx = px + dx, ny = py + dy;
                                if (nx >= 0 && nx < pW && ny >= 0 && ny < pH) {
                                    const nIdx = (ny * pW + nx) * 4;
                                    if (pData[nIdx + 3] < 128) {
                                        isEdge = true; break;
                                    }
                                }
                            }
                            if (isEdge) edgePixels.push({px, py});
                        }
                    }
                }

                // Also include a band just inside the edge (where the jigsaw cut shape is)
                const shapePixels = [];
                for (let py = 0; py < pH; py += 3) {
                    for (let px = 0; px < pW; px += 3) {
                        const idx = (py * pW + px) * 4;
                        if (pData[idx + 3] > 128) {
                            // Check if near a transparent region
                            let nearTransparent = false;
                            for (let dy = -3; dy <= 3; dy++) {
                                for (let dx = -3; dx <= 3; dx++) {
                                    const nx = px + dx, ny = py + dy;
                                    if (nx >= 0 && nx < pW && ny >= 0 && ny < pH) {
                                        const nIdx = (ny * pW + nx) * 4;
                                        if (pData[nIdx + 3] < 128) {
                                            nearTransparent = true; break;
                                        }
                                    }
                                }
                                if (nearTransparent) break;
                            }
                            if (nearTransparent) shapePixels.push({px, py});
                        }
                    }
                }

                const maxOffset = bgW - pW;
                let bestOffset = 0;
                let maxDiff = -1;
                const allScores = [];

                // Combine edge + shape pixels, deduplicate
                const combined = [...edgePixels, ...shapePixels];
                const seen = new Set();
                const uniquePixels = [];
                for (const p of combined) {
                    const key = p.px + ',' + p.py;
                    if (!seen.has(key)) { seen.add(key); uniquePixels.push(p); }
                }

                for (let ox = 0; ox <= maxOffset; ox++) {
                    let diff = 0;
                    let count = 0;
                    for (const {px, py} of uniquePixels) {
                        const pIdx = (py * pW + px) * 4;
                        const bgIdx = (py * bgW + (ox + px)) * 4;
                        diff += Math.abs(pData[pIdx] - bgData[bgIdx])
                              + Math.abs(pData[pIdx+1] - bgData[bgIdx+1])
                              + Math.abs(pData[pIdx+2] - bgData[bgIdx+2]);
                        count++;
                    }
                    if (count > 0) {
                        const avgDiff = diff / count;
                        allScores.push({offset: ox, diff: Math.round(avgDiff), count});
                        if (avgDiff > maxDiff) {
                            maxDiff = avgDiff;
                            bestOffset = ox;
                        }
                    }
                }

                // Find local maxima in a neighborhood to avoid noise spikes
                const smoothed = [];
                for (let i = 2; i < allScores.length - 2; i++) {
                    const window = allScores.slice(i-2, i+3);
                    const avg = window.reduce((s, w) => s + w.diff, 0) / window.length;
                    smoothed.push({offset: allScores[i].offset, diff: avg});
                }
                let smoothBest = 0, smoothMax = -1;
                for (const s of smoothed) {
                    if (s.diff > smoothMax) { smoothMax = s.diff; smoothBest = s.offset; }
                }

                return {
                    gap: bestOffset,
                    smoothedGap: smoothBest,
                    maxDiff: Math.round(maxDiff),
                    edgePixelCount: edgePixels.length,
                    shapePixelCount: shapePixels.length,
                    uniquePixelCount: uniquePixels.length,
                    top5: allScores.sort((a,b) => b.diff - a.diff).slice(0,5),
                };
            })()
        """,
    })
    data = json.loads(result.output or "{}")
    log(f"Edge result: {json.dumps(data)}")
    return data.get("gap")


async def try_drag(browser: AgentBrowserTool, computer: ComputerTool, distance: int) -> bool:
    """Attempt a drag and check if CAPTCHA is solved."""
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
        has_captcha = await robust_login(browser)
        if not has_captcha:
            log("No CAPTCHA appeared.")
            await browser({"action": "screenshot", "path": os.path.join(download_dir, "no_captcha.png")})
            return

        gap = await get_gap_by_edge(browser)
        if gap is not None:
            handle_drag = int(gap * 280 / 262)
            log(f"\nEdge suggests gap={gap}px, handle_drag={handle_drag}px")
            if await try_drag(browser, computer, handle_drag):
                log("SUCCESS with edge!")
                return
            # Try nearby on same instance
            for offset in [-10, 10, -20, 20]:
                if await try_drag(browser, computer, handle_drag + offset):
                    log(f"SUCCESS with offset {offset}!")
                    return
                await asyncio.sleep(1.0)

        log("\nEdge failed. Trying brute force...")
        for dist in range(100, 261, 10):
            if await try_drag(browser, computer, dist):
                log(f"SUCCESS at {dist}px!")
                return
            await asyncio.sleep(2.0)
            result = await browser({
                "action": "eval",
                "js": "(() => ({hasVerify: !!document.querySelector('.xftImageVerify')}))()",
            })
            if not json.loads(result.output or "{}").get("hasVerify"):
                log("CAPTCHA disappeared unexpectedly")
                return

        log("\nAll methods failed.")

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
