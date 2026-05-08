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

## Next Steps

1. Update `computer.py` `mouse_drag` to allow configurable delay between steps (currently hardcoded at 10ms).
2. Update system prompt to instruct LLM to use high `steps` (e.g., 80-100) for CAPTCHA drags.
3. End-to-end test: Login → screenshot → LLM visual analysis → compute gap offset → slow drag → verify.
4. The gap offset can be computed by:
   - LLM analyzes screenshot to find handle and gap positions in screen pixels
   - Or use OpenCV template matching between `.bottomImage` and `.dragImage`
