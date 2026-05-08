# xft.cmbchina.com

## Slider CAPTCHA

- First call `agent_browser` with `action='focus'` to bring the browser window to the foreground.
- Then use `computer.mouse_drag` for an OS-level drag at exact screen coordinates.
- The drag generates trusted mouse events that bypass `isTrusted` checks.
- Coordinates from the screenshot are absolute screen pixels (the screenshot is the primary monitor).
- The drag must be SLOW so the page JavaScript can track the movement.
  Use `steps=80` and `delay_ms=25` (total ~2 seconds).
- Drag the handle (`.imageVerifyDragButton`), NOT the puzzle image itself.

### Coordinate conversion

Use eval JS to get exact element positions:
- `querySelector('.imageVerifyDragButton')` for the handle
- `querySelector('.bottomImage')` for the background

Convert viewport to screen coordinates:

```
screenX = window.screenX + (window.outerWidth - window.innerWidth) / 2 + rect.left
screenY = window.screenY + window.outerHeight - window.innerHeight
        - (window.outerWidth - window.innerWidth) / 2 + rect.top
```

Then use `computer.mouse_drag` with those exact screen coordinates.

### Scaling factor (critical)

The handle does NOT move 1:1 with the puzzle piece:
- Background: 340px wide
- Puzzle piece: 78px wide
- Handle: 60px wide
- Track: 340px

Puzzle movement range = 340 - 78 = 262px  
Handle movement range = 340 - 60 = 280px

Therefore:

```
handle_drag = puzzle_gap * 280 / 262 ~= puzzle_gap * 1.069
```

If you visually estimate the gap is 200px, drag the handle 214px (NOT 200px).

### Visual analysis

The screenshot shows the full desktop. Locate the CAPTCHA modal (usually centered):
- The puzzle piece starts on the left side of the background image.
- The gap is a missing piece on the right side.
- Visually estimate the horizontal pixel distance from the puzzle piece's left edge to the gap's left edge.
- Multiply by 1.069 to get the handle drag distance.
- Add this to the handle's start screenX to get the drag `end_x`.
- The drag must be horizontal (same y coordinate).
- After dragging, wait 2 seconds and check the screenshot:
  - If the CAPTCHA modal is gone, success.
  - If still present, try adjusting the estimate by +/-10px and drag again.

## Login flow

Verified working steps:

1. Click "登录" to open the form.
2. eval JS to fill phone/password using `HTMLInputElement.prototype.value` setter + `dispatchEvent(input/change)`.
3. Click the submit button via eval with:
   ```js
   document.querySelector('.PasswordLogin_loginBtn__yuCsm').click()
   ```
   Do NOT use `target_text='登录'` for the submit button because the page has multiple "登录" elements.
4. If CAPTCHA appears, use pixeldiff via eval JS canvas comparison for gap estimation, then apply the 1.069 scaling factor and try +/-10px offsets.
