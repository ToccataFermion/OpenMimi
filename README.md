# OpenMimi

Local Windows AI Agent powered by Anthropic's tool_use protocol.

OpenMimi connects an LLM to a rich set of browser and desktop automation tools,
allowing the AI to navigate websites, fill forms, solve CAPTCHAs, control the
Windows desktop, read screen text via OCR, and manage windows.

## Architecture

```
LLM <-> sampling_loop <-> ToolCollection
                        |
            +-----------+-----------+
            |                       |
    AgentBrowserTool         ComputerTool
    (Chromium/CDP)           (Windows Desktop)
```

- **`loop.py`**: LLM-driven sampling loop with tool_use / tool_result protocol
- **`AgentBrowserTool`**: Wraps vercel-labs/agent-browser Rust CLI for Chromium automation
- **`ComputerTool`**: Windows desktop automation via mss screenshots + SendInput
- **`ToolCollection`**: Unified registration and dispatch

## Quick Start

```bash
pip install -e .

# Start multi-turn chat REPL (shortest command)
mimi

# Or use the full CLI
openmimi chat          # Multi-turn REPL
openmimi run "task"    # Single task execution
openmimi replay <id>   # Replay a session

# Set credentials for xft demo (optional)
export XFT_PHONE="your_phone"
export XFT_PASSWORD="your_password"

# Run the advanced xft login demo
python scripts/xft_advanced_login.py
```

## Browser Capabilities

### Navigation & Interaction
| Action | Description |
|--------|-------------|
| `navigate` | Load a URL |
| `back` / `forward` / `reload` | Browser navigation |
| `click` | Click by accessibility ref or text match |
| `right_click` | Right-click by accessibility ref or text match |
| `double_click` | Double-click by accessibility ref or text match |
| `check` / `uncheck` | Toggle checkboxes (never click them) |
| `type` / `fill` | Type text into inputs |
| `react_fill` | React-aware fill (prototype setter + events) |
| `press` | Press a key (Enter, Escape, Tab, etc.) |
| `key_combo` | Press multiple keys simultaneously (e.g. `['Control','a']`) |
| `hover` | Hover over an element |
| `scroll` | Scroll the page |
| `drag` | Drag and drop between elements |
| `select` | Select dropdown options |
| `upload` | Upload a file |
| `download` | Download a file |

### Page Analysis
| Action | Description |
|--------|-------------|
| `snapshot` | Accessibility tree with @eN refs |
| `screenshot` | Capture viewport (with optional annotation) |
| `extract` | Structured extraction: text, headings, links, forms, tables, metadata, images |
| `page_source` | Raw HTML of the current page |
| `get_url` | Current page URL |
| `get_title` | Current page title |
| `get_attribute` | Read a DOM attribute (href, src, data-*, etc.) |
| `set_attribute` | Write a DOM attribute |
| `get_property` | Read a JS property (value, checked, innerText, etc.) |
| `get_box` | Element bounding box for OS-level mouse coordination |
| `visual_locate` | Find element by OpenCV template matching on screenshot |
| `scroll_into_view` | Bring element into viewport |
| `scroll_until` | Scroll until element/text appears (infinite scroll friendly) |
| `wait_for` | Poll until element/text appears |
| `wait_for_navigation` | Wait for URL change after SPA navigation |
| `wait_for_network_idle` | Wait until no active network requests for a duration |

### Network & Debugging
| Action | Description |
|--------|-------------|
| `network_log` | Intercept fetch/XHR requests and capture response status/body |
| `network_modify` | Inject headers, block URLs, mock responses, override UA |
| `console` | Capture recent browser console logs |
| `pdf` | Save page as PDF |
| `eval` | Evaluate JavaScript and return result |
| `cdp` | Send arbitrary Chrome DevTools Protocol commands |
| `batch` | Execute multiple commands atomically |

### Tabs & Session
| Action | Description |
|--------|-------------|
| `tab_list` / `tab_switch` / `tab_new` / `tab_close` | Tab management |
| `save_session` / `load_session` | JSON-based cookie/storage persistence |
| `clear_cache` | Wipe cookies, localStorage, sessionStorage |
| `storage` | Read/write/delete localStorage, sessionStorage, cookies (CDP-first for cookies) |

### Anti-Detection & Stealth
| Feature | Description |
|---------|-------------|
| `stealth` mode | 14-vector JS anti-detection injection |
| `emulate_device` | Mobile emulation: iPhone 14, Pixel 7, iPad Mini |
| `set_timezone` | Override browser timezone via CDP |
| `set_locale` | Override browser locale via CDP |
| `set_geolocation` | Override GPS location via CDP |
| `proxy` | Route traffic through proxy server |
| `user_data_dir` | Persistent Chrome profile (IndexedDB, cache, extensions) |
| `screenshot_scale` | Scale screenshots to reduce LLM token usage |
| `slow_mo_ms` | Randomized delay after each action for human-like pacing |
| Auto-retry | Exponential backoff for transient CDP failures |
| Auto-screenshot-on-error | Captures state on failure for debugging |

## ComputerUse Capabilities

| Action | Description |
|--------|-------------|
| `screenshot` | Capture primary monitor |
| `mouse_move` [humanize] / `mouse_click` [wander] / `mouse_drag` | Mouse control with human-like trajectories |
| `mouse_scroll` / `mouse_double_click` | Extended mouse actions |
| `key_press` / `key_combo` / `type` | Keyboard input (Unicode-aware via clipboard paste) |
| `cursor_position` | Get current mouse coordinates |
| `focus_window` / `window_manage` | Window focus, move, resize, minimize, maximize, close |
| `list_windows` | Enumerate visible windows |
| `locate` | OpenCV template matching on screen |
| `ocr` | Tesseract OCR for text extraction (chi_sim+eng supported) |
| `click_text` | Find text on screen via OCR and click it |
| `click_image` | Find image template on screen via OpenCV and click it |
| `clipboard` | Read/write system clipboard |
| `launch` | Start applications by name or path |
| `file` | Read/write files on disk |
| `get_screen_info` | Primary monitor resolution and DPI |
| `shell` | Execute shell commands with timeout |
| `batch` | Execute multiple actions in one tool call |

## System Prompt

The default system prompt (`loop.py`) includes detailed guidance for:
- Browser automation best practices
- React SPA click fallbacks and `react_fill` for controlled inputs
- xft.cmbchina.com login flow and CAPTCHA solving
- Slider CAPTCHA physics (scaling factor, slow drag, OS-level events)
- Tool timeout configuration (`OPENMIMI_TOOL_TIMEOUT_S`)

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `XFT_PHONE` | Phone number for xft login | (fallback in scripts) |
| `XFT_PASSWORD` | Password for xft login | (fallback in scripts) |
| `OPENMIMI_TOOL_TIMEOUT_S` | Per-tool timeout (seconds) | 300 |
| `OPENMIMI_BROWSER_TRACE` | Print browser phase timings | off |

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/solve_captcha_dl.py` | Basic xft login with DL CAPTCHA solver |
| `scripts/explore_xft_workbench.py` | Workbench exploration after login |
| `scripts/xft_advanced_login.py` | Production-grade login with all features |

## xft.cmbchina.com CAPTCHA

The slider CAPTCHA on xft.cmbchina.com requires:
1. **Scaling factor**: Handle drag = puzzle_gap × 280/262 ≈ 1.069
2. **Slow drag**: steps=80, delay_ms=25 (≈2 seconds total)
3. **OS-level events**: Use `computer.mouse_drag` with exact screen coordinates
4. **Gap detection**: `captcha-recognizer` ONNX YOLO model (confidence ≥0.96)

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Syntax check
python -m py_compile src/openmimi/tools/*.py

# Run a script
PYTHONPATH=src python scripts/xft_advanced_login.py
```

## License

MIT
