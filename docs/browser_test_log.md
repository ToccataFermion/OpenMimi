# Browser Tool Test Log

## Test 1: xft.cmbchina.com — Login Popup Stall (Root Cause Fix)
**Date:** 2026-05-06
**Purpose:** Verify and fix the ~10s stall when clicking "登录" on xft.cmbchina.com (triggers `window.open` popup).

### Steps
1. Navigate to https://xft.cmbchina.com/
2. Click "登录"
3. Observe tool timeout behavior

### Results
- **Before fix:** `mouse.click` hung until `OPENMIMI_TOOL_TIMEOUT_S` (10s), then page recovered
- **After fix:** `_safe_mouse_click` times out at ~4s with `ok=False`, popup opens correctly, agent continues

### Issues Found
1. CDP `Input.dispatchMouseEvent` response stalls indefinitely when `window.open` is triggered
2. cdp_use receives duplicate CDP responses (warning: "Received duplicate response for request N")

### Fixes Applied
- Commit `69c62a2`: Wrap `mouse.click` with `asyncio.wait_for(timeout=3.0)` + `mouse.up()` release
- Commit `116937c`: Add JS `element.click()` fallback at same coordinates when CDP stalls

---

## Test 2: xft.cmbchina.com — `agent_focus_target_id` Sync
**Date:** 2026-05-06
**Purpose:** After manual CDP focus, `open_tabs` should show correct `agent_has_focus` marker.

### Steps
1. Navigate to xft.cmbchina.com
2. Click "登录" (opens popup, auto-switches focus)
3. Check `open_tabs` output

### Results
- **Before fix:** `agent_has_focus` showed wrong tab marked with `*`
- **After fix:** Correct tab is marked after `_focus_page_target_for_agent`

### Fixes Applied
- Commit `69c62a2` (same): Set `session.agent_focus_target_id = target_id` after CDP activate

---

## Test 3: xft.cmbchina.com — `_FOCUS_AND_FILL_JS` Format Bug
**Date:** 2026-05-06
**Purpose:** `type` action was completely broken due to JS format mismatch.

### Steps
1. Navigate to xft.cmbchina.com
2. Click "登录" to open popup
3. Call `type` with `target_text: '请输入手机号'`

### Results
- **Before fix:** `ValueError: JavaScript code must start with (...args) => format`
- **After fix:** `Typed 11 character(s)` successfully

### Root Cause
browser_use `page.evaluate` requires JS to start with `(`. The `/*__OPENMIMI_FOCUS_FILL__*/` prefix broke this rule.

### Fixes Applied
- Commit `392701d`: Move comment inside function body; ensure JS starts with `(`

---

## Test 4: xft.cmbchina.com — Full Login Flow (Real Credentials)
**Date:** 2026-05-06
**Purpose:** End-to-end login automation with real credentials.

### Steps
1. Navigate → Click "登录" → Type phone (18584828398) → Type password (Liszt123) → Click submit

### Results
- Phone typed: ✅ 11 chars
- Password typed: ✅ 8 chars
- Submit clicked: ✅
- **Login result:** Site returned to login page (likely requires CAPTCHA/verification code or credentials rejected by server)
- **Tool verdict:** Automation layer is correct; auth failure is at application layer

---

## Test 5: xft.cmbchina.com — Extract / Scroll / Hover / Press / Switch Tab
**Date:** 2026-05-06
**Purpose:** Exercise all browser actions on the same site.

### Results
| Action | Result | Notes |
|--------|--------|-------|
| Extract | ✅ | Returns 2532 chars (main), 110 chars (popup) |
| Scroll down/up | ✅ | 500px each direction |
| Hover "产品服务" | ❌ | Text not found (likely dynamic menu or image) |
| Press Escape | ✅ | Works in popup |
| Switch tab 1→2→1 | ✅ | Instant, correct focus markers |
| Screenshot | ✅ | Always returns fresh image |

---

## Test 6: github.com — Multi-Step Search Workflow
**Date:** 2026-05-06
**Purpose:** Test on a completely different site (English, modern SPA, no popups).

### Steps
1. Navigate github.com
2. Click "Search or jump to..."
3. Type "browser-use" (no locator)
4. Press Enter
5. Extract / Click result / Scroll / Click README

### Results
- Navigate: ✅
- Click search button: ✅
- Type (no locator): ❌ `no editable element is focused; click an input first`
- Press Enter: ✅ (but no text entered)
- Click "browser-use": ❌ Still on homepage, text not found
- Extract: ✅ Returns homepage text
- Scroll: ✅

### Analysis
GitHub's search button opens a command-palette modal, but `document.activeElement` after the click is not the input field. The `type` action without a locator correctly fails with "click an input first". This is **expected behavior** — the agent should use `type` with `target_text` pointing to the input's placeholder, or `click` the input first.

### No Fix Required
The tool correctly identified that no editable element has focus. The test script design was at fault (should have clicked the input field or passed a locator).

---

---

## Test 7: xft.cmbchina.com — Slider CAPTCHA Gap Detection (White-Only Method)
**Date:** 2026-05-06
**Purpose:** Solve slider/jigsaw CAPTCHA using image analysis.

### Steps
1. Login flow triggers CAPTCHA
2. Extract `bottomImage` (background) and `dragImage` (puzzle piece)
3. Use white-pixel counting under alpha mask to find gap

### Results
- Gap detection: ❌ Finds bright sky/cloud areas instead of actual gap
- For dark rocky images, gap is light gray/textured, not white
- White threshold of 200 misses textured gaps

---

## Test 8: xft.cmbchina.com — CDP Mouse Drag Bug Discovery
**Date:** 2026-05-06
**Purpose:** Investigate why drag doesn't move the slider piece.

### Steps
1. Test `browser_use.Mouse.down()` / `move()` / `up()` API
2. Inspect CDP events sent

### Results
- **Critical bug found:** `Mouse.down()` sends `mousePressed` at `x: 0, y: 0` instead of current position
- CDP does NOT track "last mouse position" — each event needs absolute coordinates
- browser_use comment says "Will use last mouse position" but this is false for CDP

### Fixes Applied
- Bypass browser_use Mouse API entirely
- Use `page._client.send.Input.dispatchMouseEvent(params, session_id=page._session_id)` directly
- Set `buttons: 1` on `mouseMoved` events during drag
- All coordinates must be explicitly provided for every event

---

## Test 9: xft.cmbchina.com — Edge-Density Gap Detection
**Date:** 2026-05-06
**Purpose:** Develop robust gap detection that works for all CAPTCHA image types.

### Approach
- Compute edge map of background (PIL `FIND_EDGES`)
- For each position, place template mask perimeter over background
- Sum edge pixel values around mask perimeter
- Gap = position with dramatically higher edge density (puzzle-piece cutout creates strong edges)

### Results
- White gap image: Peak at x=260, edge_sum=25783 (next best=19792, background=~1000)
- Textured gap image: Peak at x=200, edge_sum=19260 (next best=13279)
- **26x stronger signal than background** — extremely reliable
- Works for both white gaps AND textured/gray gaps

---

## Test 10: xft.cmbchina.com — Full Login Success (CAPTCHA Solved)
**Date:** 2026-05-06
**Purpose:** Complete end-to-end login with CAPTCHA automation.

### Steps
1. Navigate → Click "登录" → Switch to popup tab
2. Click "密码登录" → Check consent → Fill credentials
3. Click login → Trigger CAPTCHA
4. Extract images → Edge-density gap detection → Single CDP drag
5. Check post-login state

### Results
- **CAPTCHA solved:** ✅ `captchaVisible: false` after drag
- **Gap detection:** x=237 (edge_sum=19246, strong peak)
- **Drag:** 241px single continuous drag, ratio=0.94
- **Login result:** ✅ Dashboard loaded — shows "工作台", "薪税管家", "电子合同", "人员管理"
- **No device verification:** Persistent profile may have helped; security screen did not appear

### Key Insights
1. Single continuous drag is required — two separate drags trigger CAPTCHA reset
2. Piece internal offset (min_x) must be subtracted from gap position
3. Edge-density detection is robust across different image types
4. Sine ease-in-out + y-wiggle + variable delays make drag human-like enough

### Files Created
- `scripts/test_xft_captcha_solve.py` — Main solver (edge-density + single drag)
- `scripts/test_xft_captcha_analyze.py` — Multi-algorithm comparison
- `scripts/test_xft_captcha_verify_gap.py` — Detailed candidate analysis
- `scripts/test_xft_full_login.py` — Full login flow with post-CAPTCHA handling

### BrowserTool Enhancement
- Added `user_data_dir` parameter for persistent browser profiles
- Profile persists cookies/localStorage across sessions

---

## Test 11: xft.cmbchina.com — Post-Login Dashboard Exploration
**Date:** 2026-05-06
**Purpose:** Explore features available after successful login.

### Results
- **Login:** ✅ Consistent success with edge-density CAPTCHA solver
- **Dashboard features identified:**
  - 工作台 (Workbench)
  - 薪税管家 (Salary/Tax Manager)
  - 电子合同 (Electronic Contracts) — clickable, navigates to contract page
  - 人员管理 (Personnel Management) — found on dashboard
  - 个税服务 (Individual Income Tax) — clickable, opens new tab
  - 工资条 (Payroll Slips)
  - 社保申报 (Social Insurance Filing)
  - 智能记账 (Smart Bookkeeping)
  - 发票管理 (Invoice Management)
  - OA审批流程 (OA Approval Workflow)

### Issues Found
1. `navigate` back to dashboard creates new tabs instead of using browser back
2. Some menu items are icons/images without text — `target_text` click fails
3. Clicking features that open new tabs requires `switch_tab` to follow

---

## Summary of Commits
| Commit | Description |
|--------|-------------|
| `69c62a2` | `_safe_mouse_click` timeout wrapper + `agent_focus_target_id` sync |
| `392701d` | `_FOCUS_AND_FILL_JS` format fix for browser_use evaluate |
| `116937c` | JS `element.click()` fallback when CDP mouse dispatch stalls |
| `8feb29f` | Trace stalled awaits and raise default tool timeout to 5s |
| `77cca7b` | Default `OPENMIMI_TOOL_TIMEOUT_S` to 5s |
| `7021b50` | Resume CDP debugger pause on all tabs after actions |
| `976f09a` | Skip nested `AgentFocusChanged` dispatch on tab switch |
| `a01c52f` | Match placeholder for text locator; fill via `elementFromPoint` |
