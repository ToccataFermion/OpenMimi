"""Analyze CAPTCHA images with OpenCV to find gap position."""
from __future__ import annotations

import cv2
import numpy as np
import os


def find_gap_opencv(bg_path: str, piece_path: str) -> int | None:
    """Find gap position in background image."""
    bg = cv2.imread(bg_path, cv2.IMREAD_UNCHANGED)
    piece = cv2.imread(piece_path, cv2.IMREAD_UNCHANGED)

    if bg is None or piece is None:
        print("Failed to load images")
        return None

    print(f"BG shape: {bg.shape}, dtype: {bg.dtype}")
    print(f"Piece shape: {piece.shape}, dtype: {piece.dtype}")

    # If images have alpha channel, use it
    bg_gray = cv2.cvtColor(bg[:, :, :3], cv2.COLOR_BGR2GRAY) if bg.shape[2] == 4 else cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)

    if piece.shape[2] == 4:
        piece_bgr = piece[:, :, :3]
        piece_alpha = piece[:, :, 3]
        # Only consider non-transparent parts of the puzzle piece
        piece_mask = piece_alpha > 128
        piece_gray = cv2.cvtColor(piece_bgr, cv2.COLOR_BGR2GRAY)
    else:
        piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
        piece_mask = np.ones_like(piece_gray, dtype=bool)

    bg_h, bg_w = bg_gray.shape
    piece_h, piece_w = piece_gray.shape

    print(f"BG: {bg_w}x{bg_h}, Piece: {piece_w}x{piece_h}")

    # Method 1: Template matching on grayscale
    result = cv2.matchTemplate(bg_gray, piece_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    print(f"Template match (CCOEFF): x={max_loc[0]}, confidence={max_val:.4f}")

    # Method 2: Edge-based matching
    bg_edges = cv2.Canny(bg_gray, 50, 150)
    piece_edges = cv2.Canny(piece_gray, 50, 150)
    result_edge = cv2.matchTemplate(bg_edges, piece_edges, cv2.TM_CCOEFF_NORMED)
    _, max_val_e, _, max_loc_e = cv2.minMaxLoc(result_edge)
    print(f"Edge template match: x={max_loc_e[0]}, confidence={max_val_e:.4f}")

    # Method 3: Look for white/bright gap area in background
    # The gap might be a white or light-colored area
    _, bright_mask = cv2.threshold(bg_gray, 230, 255, cv2.THRESH_BINARY)
    bright_cols = bright_mask.sum(axis=0)
    print(f"Bright pixels per column (max={bright_cols.max()}):")
    for x in range(bg_w):
        if bright_cols[x] > 50:
            print(f"  x={x}: {bright_cols[x]} bright pixels")

    # Method 4: Try to find the distinctive notch/border of the gap
    # The gap boundary should have strong edges
    sobel_x = cv2.Sobel(bg_gray, cv2.CV_64F, 1, 0, ksize=3)
    edge_intensity = np.abs(sobel_x).mean(axis=0)
    print(f"\nEdge intensity peaks:")
    for x in range(1, bg_w - 1):
        if edge_intensity[x] > edge_intensity.mean() * 2.5:
            if edge_intensity[x] > edge_intensity[x-1] and edge_intensity[x] > edge_intensity[x+1]:
                print(f"  x={x}: intensity={edge_intensity[x]:.1f}")

    # Method 5: Use alpha mask to only match where piece has content
    if piece.shape[2] == 4:
        # Create masked versions
        piece_masked = piece_gray.copy()
        piece_masked[~piece_mask] = 0

        # Try matching with mask
        result_masked = cv2.matchTemplate(bg_gray, piece_masked, cv2.TM_CCOEFF_NORMED)
        _, max_val_m, _, max_loc_m = cv2.minMaxLoc(result_masked)
        print(f"\nMasked template match: x={max_loc_m[0]}, confidence={max_val_m:.4f}")

    # Save debug images
    debug = bg.copy()
    cv2.rectangle(debug, max_loc, (max_loc[0] + piece_w, max_loc[1] + piece_h), (0, 0, 255), 2)
    cv2.imwrite("data/captcha_match_debug.png", debug)
    print(f"\nDebug image saved: data/captcha_match_debug.png")

    return max_loc[0]


if __name__ == "__main__":
    base = os.path.join(os.path.dirname(__file__), "..", "data")
    bg_path = os.path.join(base, "captcha_bg.png")
    piece_path = os.path.join(base, "captcha_drag.png")

    gap_x = find_gap_opencv(bg_path, piece_path)
    if gap_x is not None:
        print(f"\nEstimated gap position: x={gap_x}")
