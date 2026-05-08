"""Find gap position in CAPTCHA by analyzing images with OpenCV."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool

try:
    import cv2
    import numpy as np
    HAS_CV = True
except ImportError:
    HAS_CV = False


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def find_gap(bg_b64: str, piece_b64: str) -> int | None:
    """Try multiple methods to find gap position."""
    if not HAS_CV:
        return None

    bg_data = base64.b64decode(bg_b64.split(",")[1] if "," in bg_b64 else bg_b64)
    piece_data = base64.b64decode(piece_b64.split(",")[1] if "," in piece_b64 else piece_b64)

    bg = cv2.imdecode(np.frombuffer(bg_data, np.uint8), cv2.IMREAD_COLOR)
    piece = cv2.imdecode(np.frombuffer(piece_data, np.uint8), cv2.IMREAD_COLOR)

    if bg is None or piece is None:
        log("Failed to decode images")
        return None

    log(f"BG shape: {bg.shape}, Piece shape: {piece.shape}")
    cv2.imwrite("data/captcha_bg.png", bg)
    cv2.imwrite("data/captcha_piece.png", piece)

    # Method 1: Look for high-contrast edges in bg that might indicate gap
    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)

    # The gap might appear as a bright/dark border. Let's look at horizontal edge intensity
    sobel_x = cv2.Sobel(bg_gray, cv2.CV_64F, 1, 0, ksize=3)
    edge_intensity = np.abs(sobel_x).mean(axis=0)

    # Find peaks in edge intensity (potential gap boundaries)
    from scipy.signal import find_peaks
    peaks, properties = find_peaks(edge_intensity, height=edge_intensity.mean() * 1.5, distance=20)
    log(f"Edge peaks at x-positions: {peaks.tolist()}")
    log(f"Peak intensities: {properties['peak_heights'].tolist() if 'peak_heights' in properties else []}")

    # Method 2: Template matching on edges
    bg_edges = cv2.Canny(bg_gray, 50, 150)
    piece_edges = cv2.Canny(piece_gray, 50, 150)
    result = cv2.matchTemplate(bg_edges, piece_edges, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    log(f"Edge template match: x={max_loc[0]}, confidence={max_val:.3f}")

    # Method 3: Try matching piece against bg in different ways
    for method_name, method in [("CCOEFF", cv2.TM_CCOEFF_NORMED), ("CCORR", cv2.TM_CCORR_NORMED), ("SQDIFF", cv2.TM_SQDIFF_NORMED)]:
        result = cv2.matchTemplate(bg_gray, piece_gray, method)
        if method == cv2.TM_SQDIFF_NORMED:
            _, min_val, _, min_loc = cv2.minMaxLoc(result)
            log(f"Grayscale {method_name}: x={min_loc[0]}, val={min_val:.3f}")
        else:
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            log(f"Grayscale {method_name}: x={max_loc[0]}, val={max_val:.3f}")

    # Method 4: Look for white/transparent areas in the bg (some CAPTCHAs use white gaps)
    _, white_mask = cv2.threshold(bg_gray, 240, 255, cv2.THRESH_BINARY)
    white_cols = white_mask.sum(axis=0)
    white_peaks, _ = find_peaks(white_cols, height=100, distance=20)
    log(f"White gap candidates at x: {white_peaks.tolist()}")

    # Method 5: Try to find where the piece was cut from by comparing pixel values
    # The gap area in bg should have different colors than the piece
    # Slide the piece over bg and compute difference
    h, w = piece_gray.shape
    bg_h, bg_w = bg_gray.shape
    min_diff = float('inf')
    best_x = 0
    for x in range(bg_w - w + 1):
        diff = np.abs(bg_gray[:, x:x+w].astype(np.int16) - piece_gray.astype(np.int16)).mean()
        if diff < min_diff:
            min_diff = diff
            best_x = x
    log(f"Min difference match: x={best_x}, diff={min_diff:.1f}")

    # Method 6: Look for the distinctive notch/shape
    # Use Harris corner detection on bg to find the gap corners
    corners = cv2.cornerHarris(bg_gray, 2, 3, 0.04)
    corner_cols = (corners > 0.01 * corners.max()).sum(axis=0)
    corner_peaks, _ = find_peaks(corner_cols, height=5, distance=20)
    log(f"Corner peaks at x: {corner_peaks.tolist()}")

    return None


async def main() -> None:
    if not HAS_CV:
        log("OpenCV not available")
        return

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
                    if (btn) { btn.click(); return {clicked: true, class: btn.className}; }
                    return {clicked: false};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        log("\n=== Get CAPTCHA images ===")
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const bg = document.querySelector('.bottomImage');
                    const drag = document.querySelector('.dragImage');
                    function getSrc(el) {
                        if (!el) return null;
                        if (el.src) return el.src;
                        const style = window.getComputedStyle(el);
                        const m = style.backgroundImage.match(/url\(["']?(data:image\/[^"']+)["']?\)/);
                        return m ? m[1] : null;
                    }
                    return {
                        bgSrc: getSrc(bg),
                        dragSrc: getSrc(drag),
                        bgRect: bg ? {left: bg.getBoundingClientRect().left, top: bg.getBoundingClientRect().top, width: bg.getBoundingClientRect().width, height: bg.getBoundingClientRect().height} : null,
                        dragRect: drag ? {left: drag.getBoundingClientRect().left, top: drag.getBoundingClientRect().top, width: drag.getBoundingClientRect().width, height: drag.getBoundingClientRect().height} : null,
                    };
                })()
            """,
        })
        data = json.loads(result.output or "{}")
        log(f"Data: {json.dumps({k: v[:50] + '...' if isinstance(v, str) and len(v) > 50 else v for k, v in data.items()}, ensure_ascii=False)}")

        bg_src = data.get("bgSrc")
        drag_src = data.get("dragSrc")

        if bg_src and drag_src:
            find_gap(bg_src, drag_src)
        else:
            log("Missing image sources")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
