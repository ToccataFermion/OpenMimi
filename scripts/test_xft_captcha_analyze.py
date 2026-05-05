"""Analyze CAPTCHA images and test multiple gap detection algorithms."""
from __future__ import annotations

import json
import os
import sys
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def load_images():
    bottom = Image.open("xft_bottom_image.png").convert("RGBA")
    drag = Image.open("xft_drag_image.png").convert("RGBA")
    return bottom, drag


def get_mask_coords(drag: Image.Image):
    """Get non-transparent pixel coordinates relative to mask bbox."""
    alpha = drag.split()[3]
    mask_pixels = alpha.load()
    tm_w, tm_h = drag.size
    min_x, min_y = tm_w, tm_h
    max_x, max_y = 0, 0
    for y in range(tm_h):
        for x in range(tm_w):
            if mask_pixels[x, y] > 128:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    mask_w = max_x - min_x + 1
    mask_h = max_y - min_y + 1
    coords = []
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if mask_pixels[x, y] > 128:
                coords.append((x - min_x, y - min_y))
    return min_x, min_y, mask_w, mask_h, coords


def alg_white_pixels(bg: Image.Image, min_x, min_y, mask_w, mask_h, mask_coords):
    """Original: count white pixels under mask."""
    bg_gray = bg.convert("L")
    bg_w, bg_h = bg_gray.size
    WHITE_THRESHOLD = 200
    scores = []
    search_start = mask_w
    search_end = bg_w - mask_w + 1
    for x in range(search_start, search_end):
        white_count = 0
        total_brightness = 0
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                b = bg_gray.getpixel((px, py))
                total_brightness += b
                if b > WHITE_THRESHOLD:
                    white_count += 1
        avg_bright = total_brightness / len(mask_coords)
        white_ratio = white_count / len(mask_coords)
        score = white_ratio * 1000 + avg_bright
        scores.append((x, score, avg_bright, white_ratio))
    scores.sort(key=lambda s: s[1], reverse=True)
    return scores[0][0], scores[:10]


def alg_low_variance(bg: Image.Image, min_x, min_y, mask_w, mask_h, mask_coords):
    """Find where masked area has lowest variance (solid fill color)."""
    bg_gray = bg.convert("L")
    bg_w, bg_h = bg_gray.size
    scores = []
    search_start = mask_w
    search_end = bg_w - mask_w + 1
    for x in range(search_start, search_end):
        values = []
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                values.append(bg_gray.getpixel((px, py)))
        if len(values) < 5:
            continue
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        score = -var + mean * 0.5  # low variance but not completely black
        scores.append((x, score, var, mean))
    scores.sort(key=lambda s: s[1], reverse=True)
    return scores[0][0], scores[:10]


def alg_edge_density(bg: Image.Image, min_x, min_y, mask_w, mask_h, mask_coords):
    """Find where edges are strongest around mask perimeter."""
    bg_gray = bg.convert("L")
    bg_w, bg_h = bg_gray.size
    # Compute edge map
    edges = bg_gray.filter(ImageFilter.FIND_EDGES)
    edge_pixels = edges.load()
    scores = []
    search_start = mask_w
    search_end = bg_w - mask_w + 1
    # Build perimeter set
    mask_set = set(mask_coords)
    perimeter = []
    for dx, dy in mask_coords:
        for ddx, ddy in [(-1,0),(1,0),(0,-1),(0,1)]:
            if (dx+ddx, dy+ddy) not in mask_set:
                perimeter.append((dx, dy))
                break
    if not perimeter:
        perimeter = mask_coords
    for x in range(search_start, search_end):
        edge_sum = 0
        for dx, dy in perimeter:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                edge_sum += edge_pixels[px, py]
        scores.append((x, edge_sum))
    scores.sort(key=lambda s: s[1], reverse=True)
    return scores[0][0], scores[:10]


def alg_local_contrast(bg: Image.Image, min_x, min_y, mask_w, mask_h, mask_coords):
    """Find where masked area is most different from nearby background."""
    bg_gray = bg.convert("L")
    bg_w, bg_h = bg_gray.size
    scores = []
    search_start = mask_w
    search_end = bg_w - mask_w + 1
    for x in range(search_start, search_end):
        # Masked pixels
        masked = []
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                masked.append(bg_gray.getpixel((px, py)))
        # Nearby non-masked pixels (offset by 10px right)
        nearby = []
        offset = 10
        for dx, dy in mask_coords:
            px = x + dx + offset
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                nearby.append(bg_gray.getpixel((px, py)))
        if len(masked) < 5 or len(nearby) < 5:
            continue
        m_mean = sum(masked) / len(masked)
        n_mean = sum(nearby) / len(nearby)
        contrast = abs(m_mean - n_mean)
        # Penalize if masked area has high variance (not a clean gap)
        m_var = sum((v - m_mean) ** 2 for v in masked) / len(masked)
        score = contrast - m_var * 0.1
        scores.append((x, score, contrast, m_var, m_mean, n_mean))
    scores.sort(key=lambda s: s[1], reverse=True)
    return scores[0][0], scores[:10]


def alg_template_match(bg: Image.Image, drag: Image.Image, min_x, min_y, mask_w, mask_h, mask_coords):
    """Cross-correlation: find where template best matches background.
    The gap should be where the template does NOT match well."""
    bg = bg.convert("RGBA")
    bg_w, bg_h = bg.size
    # Extract template RGB values
    tm_pixels = drag.load()
    bg_pixels = bg.load()

    # For each position, compute correlation between template and bg under mask
    scores = []
    search_start = mask_w
    search_end = bg_w - mask_w + 1
    for x in range(search_start, search_end):
        diff_sum = 0
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                tr, tg, tb, ta = tm_pixels[min_x + dx, min_y + dy]
                br, bg_, bb, ba = bg_pixels[px, py]
                # Weight by template alpha
                alpha_w = ta / 255.0
                diff = abs(tr - br) + abs(tg - bg_) + abs(tb - bb)
                diff_sum += diff * alpha_w
        # We want HIGH difference = gap (template doesn't match bg there)
        scores.append((x, diff_sum))
    scores.sort(key=lambda s: s[1], reverse=True)
    return scores[0][0], scores[:10]


def alg_combined(bg: Image.Image, drag: Image.Image, min_x, min_y, mask_w, mask_h, mask_coords):
    """Combine multiple signals: low variance + local contrast + brightness."""
    bg_gray = bg.convert("L")
    bg_w, bg_h = bg_gray.size
    scores = []
    search_start = mask_w
    search_end = bg_w - mask_w + 1

    for x in range(search_start, search_end):
        values = []
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                values.append(bg_gray.getpixel((px, py)))
        if len(values) < 5:
            continue
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)

        # Nearby contrast
        nearby = []
        offset = 15
        for dx, dy in mask_coords:
            px = x + dx + offset
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                nearby.append(bg_gray.getpixel((px, py)))
        n_mean = sum(nearby) / len(nearby) if nearby else mean
        contrast = abs(mean - n_mean)

        # Edge strength around perimeter
        edges = bg_gray.filter(ImageFilter.FIND_EDGES)
        edge_pixels = edges.load()
        mask_set = set(mask_coords)
        perimeter = []
        for dx, dy in mask_coords:
            for ddx, ddy in [(-1,0),(1,0),(0,-1),(0,1)]:
                if (dx+ddx, dy+ddy) not in mask_set:
                    perimeter.append((dx, dy))
                    break
        if not perimeter:
            perimeter = mask_coords
        edge_sum = 0
        for dx, dy in perimeter:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                edge_sum += edge_pixels[px, py]

        # Gap: low variance, high contrast with nearby, moderate-high brightness, some edges
        score = contrast * 2 - var * 0.5 + mean * 0.3 + edge_sum * 0.01
        scores.append((x, score, var, contrast, mean, edge_sum))
    scores.sort(key=lambda s: s[1], reverse=True)
    return scores[0][0], scores[:10]


def main():
    bottom, drag = load_images()
    print(f"Background: {bottom.size}, mode={bottom.mode}")
    print(f"Template: {drag.size}, mode={drag.mode}")

    min_x, min_y, mask_w, mask_h, mask_coords = get_mask_coords(drag)
    print(f"\nMask bbox: ({min_x}, {min_y}) size {mask_w}x{mask_h}, pixels={len(mask_coords)}")

    algorithms = [
        ("white_pixels", alg_white_pixels),
        ("low_variance", alg_low_variance),
        ("edge_density", alg_edge_density),
        ("local_contrast", alg_local_contrast),
        ("template_match", lambda bg, *args: alg_template_match(bg, drag, *args)),
        ("combined", lambda bg, *args: alg_combined(bg, drag, *args)),
    ]

    print("\n" + "=" * 60)
    for name, func in algorithms:
        best_x, top = func(bottom, min_x, min_y, mask_w, mask_h, mask_coords)
        print(f"\n{name}: best_x={best_x}")
        for i, row in enumerate(top[:5]):
            print(f"  #{i+1}: x={row[0]}, details={row[1:]}")

    # Save diagnostic composite image
    composite = bottom.copy()
    draw_x = alg_combined(bottom, drag, min_x, min_y, mask_w, mask_h, mask_coords)[0]
    from PIL import ImageDraw
    d = ImageDraw.Draw(composite)
    # Draw rectangle at detected position
    d.rectangle([draw_x, min_y, draw_x + mask_w, min_y + mask_h], outline="red", width=2)
    composite.save("xft_analyze_composite.png")
    print(f"\nSaved composite with detected gap at x={draw_x} to xft_analyze_composite.png")


if __name__ == "__main__":
    main()
