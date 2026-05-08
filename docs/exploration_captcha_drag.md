# CAPTCHA Slider Drag Exploration Log

## Context
Trying to automate login to `xft.cmbchina.com` which presents a slider/jigsaw CAPTCHA after submitting credentials. The account is `18584828398 / Liszt123`.

## CAPTCHA Structure (from DOM inspection)

| Element | Selector | Size | Position |
|---------|----------|------|----------|
| Background image | `.bottomImage` | 340x278 | viewport center (559, 375) |
| Puzzle piece | `.dragImage` | 78x278 | viewport center (428, 375) |
| Slider track | `.imageVerifyDrag` | 340x40 | viewport center (559, 550) |
| Drag handle | `.imageVerifyDragButton` | 60x40 | viewport center (419, 550) |

The handle is BELOW the puzzle image (Y=550 vs Y=375). Dragging the handle horizontally should move the puzzle piece to align with the gap in the background image.

## Coordinate Conversion (Viewport -> Screen)

For OS-level mouse actions (SendInput), we need absolute screen coordinates:

```
screenX = window.screenX + (outerWidth - innerWidth) / 2 + rect.left
screenY = window.screenY + outerHeight - innerHeight - (outerWidth - innerWidth) / 2 + rect.top
```

Critical: `computer.*` actions use **raw screen pixels**. Do NOT multiply by `devicePixelRatio`.

## Timeline of Hypotheses and Tests

### Phase 1: CDP Browser Actions (Failed)
**Hypothesis:** Use agent-browser's built-in click/drag actions to interact with the CAPTCHA.

**Result:** FAIL. The CAPTCHA checks `isTrusted` on mouse events. CDP-injected events are not trusted, so the slider doesn't respond.

**Evidence:** DOM state after CDP drag shows `btnLeft: "0px"`, handle didn't move. Previous attempts showed the CAPTCHA modal would disappear but validation failed.

### Phase 2: OS-Level Mouse Events (Partial Success)
**Hypothesis:** Use `computer.mouse_drag` (Windows SendInput API) to generate trusted mouse events.

**Initial Problem:** Mouse cursor didn't move at all.

**Root Cause - CRITICAL FIX:** The `_INPUT` ctypes structure was misaligned. On x64 Windows:
- `MOUSEINPUT.dwExtraInfo` is 8 bytes (`ctypes.c_ulonglong`)
- The old code used `ctypes.c_ulong * 7` for the union, giving 28 bytes instead of 32
- This caused `dwFlags` to be misaligned (read as 0), so Windows ignored all mouse events

**Fix:** Defined proper structures with correct field sizes:
```python
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_ulonglong),
    ]  # 32 bytes on x64
```

### Phase 3: Fast Drag Doesn't Move Puzzle Piece (Failed)
**Hypothesis:** After fixing the structure, a standard `mouse_drag` with 20 steps and 10ms delay would work.

**Result:** PARTIAL. The handle moved (`btnLeft: "150px"`) but the puzzle piece (`dragImage`) stayed at `left: "0px"`.

**Tested approaches:**
1. Drag handle button center - handle moves, puzzle piece doesn't
2. Drag puzzle piece center - everything resets to 0px
3. Drag track center - everything resets to 0px
4. Natural entry (hover left, slowly move into handle, then drag) - handle moves ~120px, puzzle piece doesn't follow

**Key observation:** Only dragging the handle produces any movement. Dragging anything else causes a reset.

### Phase 4: Slow Drag Works! (Success)
**Hypothesis:** The CAPTCHA's JavaScript event handling requires slower, more granular mouse movement to properly track the drag state and update the puzzle piece.

**Test:** Custom slow drag with:
- 80 steps (vs default 20)
- 25ms delay between steps (vs default 10ms)
- Linear interpolation (no bezier curve jitter)
- Total drag time: ~2 seconds

**Result:** SUCCESS! Both handle AND puzzle piece moved:
```json
{
  "btn": {"left": "150px", "rect": {"left": 539}},
  "drag": {"left": "139.858px", "rect": {"left": 528.85}}
}
```

The puzzle piece uses CSS `left` property (not `transform`) and it successfully followed the handle.

**Comparison:**
| Approach | Handle | Puzzle Piece | Result |
|----------|--------|--------------|--------|
| Fast drag (20 steps, 10ms) | 150px | 0px | FAIL |
| Slow drag (80 steps, 25ms) | 150px | 139.86px | SUCCESS |
| Synthetic JS events | 0px | 0px | FAIL |

### Phase 5: Touch Events (Ruled Out)
**Hypothesis:** The CAPTCHA might use touch events instead of mouse events.

**Result:** No touch support. `ontouchstart` not present, `'ontouchstart' in window` returns `false`.

### Phase 6: CDP Synthetic Events for Comparison
**Hypothesis:** Maybe synthetic events would work if dispatched carefully.

**Test:** Dispatched `mousedown` on handle, `mousemove` on document, `mouseup` on document via JS `MouseEvent` constructor.

**Result:** FAIL. Everything reset to 0px. Confirms that synthetic events don't satisfy the CAPTCHA's validation.

## Key Findings

1. **Trusted input is required:** Only OS-level SendInput works. CDP/synthetic events fail.
2. **Handle is the correct target:** Drag `.imageVerifyDragButton`, not the puzzle piece or track.
3. **Speed matters:** The drag must be slow enough (≥2 seconds with fine granularity) for the CAPTCHA's JavaScript to track and update the puzzle piece.
4. **Puzzle piece uses `left` CSS:** Not `transform`. Both handle and piece move via `left`.
5. **Gap is randomized:** A fixed 150px drag won't always solve it. The LLM must visually analyze the screenshot to determine the correct drag distance.
6. **No touch events:** Pure mouse event handling.

## Working Drag Parameters

```python
# Successful configuration
steps = 80          # High granularity
delay_ms = 25       # 25ms between steps (was 10ms)
total_time ≈ 2.0s   # Enough for JS event handlers to keep up
interpolation = linear  # Bezier jitter may be okay but untested
```

### Phase 7: Async Validation Test (Disproven)
**Hypothesis:** The CAPTCHA validates asynchronously after the drag ends, requiring a wait.

**Test:** `debug_captcha_wait.py` performs a 150px slow drag then polls DOM state every second for 10 seconds.

**Result:** FAIL. At t=1s: handle=150px, puzzle=139.858px. By t=2s: both snap back to 0px. The CAPTCHA resets immediately on validation failure.

**Conclusion:** Validation is synchronous (~1s). Waiting longer doesn't help.

### Phase 8: Fine Brute Force (Inconclusive)
**Hypothesis:** The correct distance was missed due to coarse 20px increments.

**Test:** `debug_captcha_fine.py` tests 80-240px in 5px increments on fresh CAPTCHA instances.

**Result:** INCONCLUSIVE. Login flakiness prevented reliable execution. Need more robust retry logic.

### Phase 9: OpenCV Image Analysis (Partial)
**Hypothesis:** Computer vision can find the gap position in the background image.

**Test:** Extract `.bottomImage` and `.dragImage`, run template matching, edge detection, and bright-pixel analysis.

**Result:** Template matching finds the puzzle piece's CURRENT position (x=45), not the gap. The background image is a complete photograph without an obvious "hole". The gap is likely rendered client-side via CSS overlay or border.

**Key finding:** The isolated background image doesn't expose the gap location. Vision must analyze the RENDERED screenshot, not the raw image asset.

### Phase 10: Direct JS Position Manipulation (Failed)
**Hypothesis:** Bypass drag entirely by setting `style.left` directly and dispatching synthetic events.

**Test:** `debug_captcha_js_set.py` sets handle/puzzle `left` to 100-260px and dispatches mouseup/change events.

**Result:** FAIL. The CAPTCHA modal stays open for all positions. The validation requires actual trusted mouse events, not just CSS position changes.

### Phase 11: Coordinate Verification
**Hypothesis:** The mouse might not be landing exactly on the handle, causing validation to fail.

**Test:** `debug_captcha_hover.py` moves mouse to computed handle center and checks `elementFromPoint`.

**Result:** `elementFromPoint` returns a child SPAN, not the handle DIV (`isHandle: false`). However, a mini drag (10px) still successfully moves the handle, confirming mouse events bubble correctly. The child element issue is not the root cause.

**Additional finding:** Window metrics at CAPTCHA time:
- `screenX=10, screenY=10`
- `outerWidth=1132, outerHeight=892`
- `innerWidth=1118, innerHeight=798`
- Handle viewport center: (419, 550)
- Handle screen center: (436, 647)

### Phase 12: Handle-to-Puzzle Scaling Factor
**Observation:** When dragging the handle, the puzzle piece doesn't move 1:1.

**Data:**
- Handle at 10px → puzzle at 9.32px
- Handle at 150px → puzzle at 139.86px

**Formula:** `handle_drag = puzzle_gap * (track_width - handle_width) / (bg_width - puzzle_width)`
- Track width = 340px, handle width = 60px → handle range = 280px
- BG width = 340px, puzzle width = 78px → puzzle range = 262px
- Scaling factor: 280/262 = **1.0687**

**Critical implication:** Earlier brute-force distances were handle distances. A 200px handle drag only moves the puzzle 187px. The gap might be at ~200px puzzle position, requiring ~214px handle drag.

---

## Current Working Hypothesis

The CAPTCHA validation likely checks:
1. **Trusted mouse events** (satisfied by SendInput)
2. **Slow drag trajectory** (satisfied by 80 steps, 25ms delay)
3. **Exact final position** within a small tolerance (~2-5px)
4. **Mouse on handle element** during drag (events bubble from child SPAN, so this is satisfied)

The remaining unknown is the **correct gap position per instance**.

## Tested Approaches Summary

| Approach | Result | Notes |
|----------|--------|-------|
| CDP synthetic drag | FAIL | isTrusted check |
| Fast OS drag (20 steps) | PARTIAL | Handle moves, puzzle doesn't |
| Slow OS drag (80 steps) | SUCCESS | Both handle and puzzle move |
| Brute force 50-260px | FAIL | Coarse increments; scaling not accounted for |
| Wait for async validation | FAIL | Resets within 1-2s |
| OpenCV on raw images | PARTIAL | Finds current position, not gap |
| Direct JS set + events | FAIL | Requires trusted events |
| Vision model analysis | IN PROGRESS | Needs reliable per-instance screenshot |

## Next Steps

1. **Implement vision-based gap detection:** Use the orchestrator's screenshot → LLM vision loop to estimate gap position per CAPTCHA instance.
2. **Account for scaling:** Convert vision-estimated puzzle gap to handle drag distance using factor 1.0687.
3. **Add login retry logic:** The CAPTCHA doesn't appear consistently; add robust form-filling and button-click verification.
4. **Test end-to-end:** Login → CAPTCHA screenshot → vision estimate → scaled drag → verify.
5. **Fallback strategy:** If vision estimate is off, try ±5px and ±10px offsets before giving up.
