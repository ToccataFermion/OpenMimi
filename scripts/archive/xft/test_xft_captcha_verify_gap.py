"""Verify gap candidates by inspecting pixel statistics at specific positions."""
from __future__ import annotations

import os
import sys
from PIL import Image, ImageFilter, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main():
    bottom = Image.open("xft_bottom_image.png").convert("RGBA")
    drag = Image.open("xft_drag_image.png").convert("RGBA")
    bg_gray = bottom.convert("L")
    bg_w, bg_h = bg_gray.size

    # Get mask
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
    mask_coords = []
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if mask_pixels[x, y] > 128:
                mask_coords.append((x - min_x, y - min_y))

    edges = bg_gray.filter(ImageFilter.FIND_EDGES)
    edge_pixels = edges.load()

    # Perimeter pixels
    mask_set = set(mask_coords)
    perimeter = []
    for dx, dy in mask_coords:
        for ddx, ddy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            if (dx + ddx, dy + ddy) not in mask_set:
                perimeter.append((dx, dy))
                break
    if not perimeter:
        perimeter = mask_coords

    def analyze_position(x):
        values = []
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                values.append(bg_gray.getpixel((px, py)))
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)

        edge_sum = 0
        for dx, dy in perimeter:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                edge_sum += edge_pixels[px, py]

        # Nearby background (shifted right by 20px)
        nearby = []
        offset = 20
        for dx, dy in mask_coords:
            px = x + dx + offset
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                nearby.append(bg_gray.getpixel((px, py)))
        n_mean = sum(nearby) / len(nearby) if nearby else mean

        return {
            "x": x,
            "mean": round(mean, 1),
            "variance": round(var, 1),
            "edge_sum": edge_sum,
            "nearby_mean": round(n_mean, 1),
            "contrast": round(abs(mean - n_mean), 1),
        }

    # Check candidates
    candidates = [57, 67, 163, 164, 200, 220, 240, 250, 255, 260, 265, 270]
    print("Position analysis:")
    print("-" * 80)
    print(f"{'x':>5} {'mean':>8} {'var':>10} {'edge_sum':>10} {'nearby':>8} {'contrast':>8}")
    print("-" * 80)
    results = []
    for x in candidates:
        r = analyze_position(x)
        results.append(r)
        print(f"{r['x']:>5} {r['mean']:>8.1f} {r['variance']:>10.1f} {r['edge_sum']:>10} {r['nearby_mean']:>8.1f} {r['contrast']:>8.1f}")

    # Compute a combined score for all positions
    print("\n" + "=" * 80)
    print("Full search with combined scoring:")
    print("-" * 80)
    print(f"{'x':>5} {'mean':>8} {'var':>10} {'edge_sum':>10} {'contrast':>8} {'score':>10}")
    print("-" * 80)

    all_scores = []
    search_start = mask_w
    search_end = bg_w - mask_w + 1
    for x in range(search_start, search_end):
        values = []
        for dx, dy in mask_coords:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                values.append(bg_gray.getpixel((px, py)))
        if len(values) < 10:
            continue
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)

        edge_sum = 0
        for dx, dy in perimeter:
            px = x + dx
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                edge_sum += edge_pixels[px, py]

        nearby = []
        offset = 20
        for dx, dy in mask_coords:
            px = x + dx + offset
            py = min_y + dy
            if 0 <= px < bg_w and 0 <= py < bg_h:
                nearby.append(bg_gray.getpixel((px, py)))
        n_mean = sum(nearby) / len(nearby) if nearby else mean
        contrast = abs(mean - n_mean)

        # Combined score: high edges, low variance, some contrast, not too dark
        # Normalize: edge_sum up to ~30000, var up to ~40000
        score = edge_sum / 1000 - var / 500 + contrast / 10 + mean / 50
        all_scores.append((x, score, mean, var, edge_sum, contrast))

    all_scores.sort(key=lambda s: s[1], reverse=True)
    for i in range(min(15, len(all_scores))):
        x, score, mean, var, edge_sum, contrast = all_scores[i]
        print(f"{x:>5} {mean:>8.1f} {var:>10.1f} {edge_sum:>10} {contrast:>8.1f} {score:>10.2f}")

    # Create visualization
    best_x = all_scores[0][0]
    composite = bottom.copy()
    d = ImageDraw.Draw(composite)
    d.rectangle([best_x, min_y, best_x + mask_w, min_y + mask_h], outline="red", width=3)
    composite.save("xft_verify_best_gap.png")
    print(f"\nSaved best gap visualization (x={best_x}) to xft_verify_best_gap.png")

    # Also create a strip showing all top candidates
    strip = Image.new("RGBA", (bg_w, bg_h + 40))
    strip.paste(bottom, (0, 0))
    d = ImageDraw.Draw(strip)
    colors = ["red", "blue", "green", "yellow", "purple"]
    for i in range(min(5, len(all_scores))):
        x = all_scores[i][0]
        color = colors[i % len(colors)]
        d.rectangle([x, min_y, x + mask_w, min_y + mask_h], outline=color, width=2)
        d.text((x, min_y + mask_h + 5), f"#{i+1} x={x}", fill=color)
    strip.save("xft_verify_top5.png")
    print(f"Saved top 5 candidates visualization to xft_verify_top5.png")


if __name__ == "__main__":
    main()
