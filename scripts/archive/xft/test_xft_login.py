"""Test script to log in to xft.cmbchina.com.

Uses JS inspection to find the consent checkbox and submit button precisely.

Usage:
    cd D:\Programs\projects\OpenMimi
    python scripts\test_xft_login.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

# Add src to path so we can import openmimi
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.browser import BrowserTool


# JS to inspect the login form structure - returns JSON string
_INSPECT_JS = """() => {
    const result = {
        tabs: [],
        inputs: [],
        buttons: [],
        clickable: [],
        checkboxes: [],
        login_text_elements: []
    };

    // Find tabs
    document.querySelectorAll('[role="tab"], .tab, .login-tab').forEach(el => {
        const r = el.getBoundingClientRect();
        result.tabs.push({text: el.innerText.trim(), tag: el.tagName, x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)});
    });

    // Find inputs
    document.querySelectorAll('input').forEach(inp => {
        const r = inp.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            result.inputs.push({
                type: inp.type,
                placeholder: inp.placeholder,
                name: inp.name,
                x: Math.round(r.left + r.width/2),
                y: Math.round(r.top + r.height/2),
                width: Math.round(r.width),
                height: Math.round(r.height)
            });
        }
    });

    // Find buttons
    document.querySelectorAll('button, input[type="submit"]').forEach(btn => {
        const r = btn.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            result.buttons.push({
                text: (btn.innerText || btn.value || '').trim(),
                tag: btn.tagName,
                x: Math.round(r.left + r.width/2),
                y: Math.round(r.top + r.height/2)
            });
        }
    });

    // Find all potentially clickable elements (broad search)
    document.querySelectorAll('button, a, [role="button"], [onclick], .btn, .submit, .login-btn, .ant-btn, div').forEach(el => {
        const r = el.getBoundingClientRect();
        const text = (el.innerText || el.value || '').trim().slice(0, 30);
        if (r.width > 0 && r.height > 0 && (r.width >= 80 || r.height >= 30 || text)) {
            result.clickable.push({
                text: text,
                tag: el.tagName,
                class: el.className.slice(0, 50),
                x: Math.round(r.left + r.width/2),
                y: Math.round(r.top + r.height/2),
                width: Math.round(r.width),
                height: Math.round(r.height)
            });
        }
    });

    // Find elements containing "登录" text anywhere
    document.querySelectorAll('body *').forEach(el => {
        if (el.children.length === 0 || el.children.length <= 2) {
            const text = (el.innerText || '').trim();
            if (text && text.includes('登录') && text.length <= 20) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    result.login_text_elements.push({
                        text: text.slice(0, 30),
                        tag: el.tagName,
                        class: el.className.slice(0, 50),
                        x: Math.round(r.left + r.width/2),
                        y: Math.round(r.top + r.height/2),
                        width: Math.round(r.width),
                        height: Math.round(r.height)
                    });
                }
            }
        }
    });

    // Find checkboxes
    document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        const r = cb.getBoundingClientRect();
        result.checkboxes.push({
            checked: cb.checked,
            x: Math.round(r.left + r.width/2),
            y: Math.round(r.top + r.height/2)
        });
    });

    return JSON.stringify(result);
}
"""


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_test_")
    tool = BrowserTool(download_dir=download_dir, headless=False)

    try:
        print("=" * 60)
        print("Step 1: Navigate to xft.cmbchina.com")
        print("=" * 60)
        result = await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        print(f"Result: {result.output}")
        await asyncio.sleep(1.0)

        print("\n" + "=" * 60)
        print("Step 2: Click '登录' (login)")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "登录"})
        print(f"Result: {result.output}")
        await asyncio.sleep(3.0)

        # Switch to popup tab if needed
        if result.details and result.details.get("tab_count", 1) > 1:
            tabs = result.details.get("open_tabs", [])
            focused_idx = None
            popup_idx = None
            for i, t in enumerate(tabs, start=1):
                if t.get("agent_has_focus"):
                    focused_idx = i
                if "#/index" in (t.get("url") or ""):
                    popup_idx = i
            if focused_idx != popup_idx and popup_idx is not None:
                print(f"Switching to popup tab {popup_idx}")
                result = await tool({"action": "switch_tab", "tab_index": popup_idx})
                print(f"Switch result: {result.output}")
                await asyncio.sleep(1.0)

        print("\n" + "=" * 60)
        print("Step 3: Click '密码登录' tab")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "密码登录"})
        print(f"Result: {result.output}")
        await asyncio.sleep(1.5)

        print("\n" + "=" * 60)
        print("Step 4: Inspect form structure via JS")
        print("=" * 60)
        page = await tool._maybe_get_page()
        inspect_raw = "{}"
        if page:
            inspect_raw = await page.evaluate(_INSPECT_JS) or "{}"
        try:
            inspect = json.loads(inspect_raw)
        except json.JSONDecodeError:
            inspect = {}
        print(f"Tabs: {inspect.get('tabs', [])}")
        print(f"Inputs: {inspect.get('inputs', [])}")
        print(f"Buttons: {inspect.get('buttons', [])}")
        print(f"Clickable elements: {inspect.get('clickable', [])}")
        print(f"Checkboxes: {inspect.get('checkboxes', [])}")
        print(f"Login text elements: {inspect.get('login_text_elements', [])}")

        print("\n" + "=" * 60)
        print("Step 5: Click consent checkbox")
        print("=" * 60)
        checkboxes = inspect.get("checkboxes", [])
        if checkboxes:
            cb = checkboxes[0]
            x, y = cb["x"], cb["y"]
            print(f"Clicking checkbox at ({x}, {y}), checked={cb.get('checked')}")
            result = await tool({"action": "click", "coordinate": [x, y]})
            print(f"Click checkbox: {result.output}")
            await asyncio.sleep(1.5)

            # Re-inspect to verify checkbox state and button status
            print("Re-inspecting after checkbox click...")
            page = await tool._maybe_get_page()
            if page:
                inspect_raw2 = await page.evaluate(_INSPECT_JS) or "{}"
                try:
                    inspect2 = json.loads(inspect_raw2)
                    cbs = inspect2.get("checkboxes", [])
                    print(f"Checkbox state after click: {cbs}")
                    login_els = inspect2.get("login_text_elements", [])
                    btn_els = [e for e in login_els if "登录" in (e.get("text") or "") and e.get("y", 0) > 350 and "密码" not in (e.get("text") or "")]
                    print(f"Login button candidates after checkbox click: {btn_els}")
                    # Update inspect for later use
                    inspect = inspect2
                except json.JSONDecodeError:
                    pass
        else:
            print("No checkbox found")

        print("\n" + "=" * 60)
        print("Step 6: Click phone field and type phone")
        print("=" * 60)
        result = await tool({"action": "click", "target_text": "请输入手机号"})
        print(f"Click phone: {result.output}")
        await asyncio.sleep(0.3)
        result = await tool({"action": "type", "text": "18584828398"})
        print(f"Type phone: {result.output}")

        print("\n" + "=" * 60)
        print("Step 7: Press Tab to move to password field")
        print("=" * 60)
        result = await tool({"action": "press", "key": "Tab"})
        print(f"Press Tab: {result.output}")
        await asyncio.sleep(0.3)

        print("\n" + "=" * 60)
        print("Step 8: Type password")
        print("=" * 60)
        result = await tool({"action": "type", "text": "Liszt123"})
        print(f"Type password: {result.output}")

        print("\n" + "=" * 60)
        print("Step 8b: JS force-fill form and trigger events")
        print("=" * 60)
        page = await tool._maybe_get_page()
        if page:
            js_fill_result = await page.evaluate("""() => {
                const allInputs = Array.from(document.querySelectorAll('input'));
                const visibleInputs = allInputs.filter(i => {
                    const r = i.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                const phoneInp = visibleInputs.find(i => i.type === 'text');
                const passInp = visibleInputs.find(i => i.type === 'password');
                const cb = visibleInputs.find(i => i.type === 'checkbox');
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');

                // Use native setter to bypass React value interception
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                function setNativeValue(el, val) {
                    if (!el) return;
                    nativeSetter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }

                setNativeValue(phoneInp, '18584828398');
                setNativeValue(passInp, 'Liszt123');
                if (cb) {
                    cb.checked = true;
                    cb.dispatchEvent(new Event('change', {bubbles: true}));
                }

                return {
                    inputDetails: visibleInputs.map(i => ({type: i.type, placeholder: i.placeholder, value: i.value})),
                    phoneSet: phoneInp ? phoneInp.value : null,
                    passSet: passInp ? passInp.value : null,
                    cbChecked: cb ? cb.checked : null,
                    btnClass: btn ? btn.className : null
                };
            }""")
            print(f"JS fill result: {js_fill_result}")
            await asyncio.sleep(1.5)
        else:
            print("No page for JS fill")

        print("\n" + "=" * 60)
        print("Step 9: Click submit button")
        print("=" * 60)

        # Find the actual login button by class
        page = await tool._maybe_get_page()
        if page:
            btn_info = await page.evaluate("""() => {
                const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                if (!btn) return null;
                const r = btn.getBoundingClientRect();
                return {
                    className: btn.className,
                    disabled: btn.disabled,
                    x: Math.round(r.left + r.width/2),
                    y: Math.round(r.top + r.height/2)
                };
            }""")
            print(f"Raw btn_info: {btn_info!r}")
            # page.evaluate may return JSON string
            if isinstance(btn_info, str):
                try:
                    btn_info = json.loads(btn_info)
                except json.JSONDecodeError:
                    btn_info = None
            print(f"Parsed btn_info: {btn_info}")

            # Strategy 1: click the actual button coordinates
            if btn_info and btn_info.get("x"):
                x, y = btn_info["x"], btn_info["y"]
                print(f"Clicking real login button at ({x}, {y})")
                result = await tool({"action": "click", "coordinate": [x, y]})
                print(f"Click result: {result.output}")

                # Wait for potential popup/dialog
                await asyncio.sleep(2.0)

                # Take screenshot to inspect state
                try:
                    screenshot_b64 = await page.screenshot(format="png")
                    import base64
                    with open("xft_step9_screenshot.png", "wb") as f:
                        f.write(base64.b64decode(screenshot_b64))
                    print("Screenshot saved to xft_step9_screenshot.png")
                except Exception as e:
                    print(f"Screenshot error: {e}")

                # Check for consent dialog with "同意" button (broad search)
                dialog_info = await page.evaluate("""() => {
                    const allEls = Array.from(document.querySelectorAll('body *'));
                    const agreeBtn = allEls.find(b => {
                        const text = (b.innerText || b.textContent || '').trim();
                        return text === '同意' && b.offsetParent !== null && b.children.length === 0;
                    });
                    if (agreeBtn) {
                        const r = agreeBtn.getBoundingClientRect();
                        return {found: true, text: '同意', tag: agreeBtn.tagName, x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
                    }
                    const cancelBtn = allEls.find(b => {
                        const text = (b.innerText || b.textContent || '').trim();
                        return text === '取消' && b.offsetParent !== null && b.children.length === 0;
                    });
                    if (cancelBtn) {
                        return {found: true, hasDialog: true, cancelX: Math.round(cancelBtn.getBoundingClientRect().left + cancelBtn.getBoundingClientRect().width/2), cancelY: Math.round(cancelBtn.getBoundingClientRect().top + cancelBtn.getBoundingClientRect().height/2)};
                    }
                    return {found: false};
                }""")
                print(f"Dialog check: {dialog_info}")

                if isinstance(dialog_info, str):
                    try:
                        dialog_info = json.loads(dialog_info)
                    except json.JSONDecodeError:
                        dialog_info = {"found": False}

                if dialog_info and dialog_info.get("found"):
                    bx, by = dialog_info["x"], dialog_info["y"]
                    print(f"Clicking dialog button '{dialog_info.get('text')}' at ({bx}, {by})")
                    result = await tool({"action": "click", "coordinate": [bx, by]})
                    print(f"Dialog click result: {result.output}")
                    await asyncio.sleep(3.0)

                # If still on login page, try JS direct dispatch
                result = await tool({"action": "extract", "instruction": "get text"})
                text_after = result.output
                if "密码登录" in text_after or "短信验证码" in text_after:
                    print("Still on login page after click. Trying JS direct click...")
                    js_result = await page.evaluate("""() => {
                        const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                        if (btn) {
                            btn.click();
                            return 'JS click dispatched on ' + btn.className;
                        }
                        return 'Button not found';
                    }""")
                    print(f"JS direct click result: {js_result}")
            else:
                print("Real login button not found, falling back to Enter key")
                result = await tool({"action": "press", "key": "Enter"})
                print(f"Press Enter: {result.output}")
        else:
            print("No page available, falling back to Enter key")
            result = await tool({"action": "press", "key": "Enter"})
            print(f"Press Enter: {result.output}")

        await asyncio.sleep(3.0)

        print("\n" + "=" * 60)
        print("Step 10: Check login result")
        print("=" * 60)
        result = await tool({"action": "extract", "instruction": "get text"})
        result_text = result.output
        # Save raw text to file for inspection
        with open("xft_login_result.txt", "w", encoding="utf-8") as f:
            f.write(result_text)
        print(f"Saved page text to xft_login_result.txt ({len(result_text)} chars)")
        # Sanitize for Windows console encoding
        safe_text = result_text.encode("utf-8", errors="ignore").decode("utf-8")
        print(safe_text[:2000])

        if "系统异常" in result_text:
            print("\n[RESULT] Server returned system error.")
            print("The credentials may be incorrect or the account may require SMS verification.")
        elif "密码错误" in result_text or "账号或密码" in result_text:
            print("\n[RESULT] Credentials rejected by server.")
        elif "登录成功" in result_text or "欢迎" in result_text:
            print("\n[RESULT] Login appears successful!")
        elif "我的" in result_text or "账户" in result_text or "个人中心" in result_text:
            print("\n[RESULT] Likely logged in.")
        elif "密码登录" not in result_text and "手机验证码登录" not in result_text and "短信验证码" not in result_text:
            print("\n[RESULT] Page changed - possible success.")
        else:
            print("\n[RESULT] Still on login page.")
            print("Remaining text:", result_text[:1000])

        print("\n" + "=" * 60)
        print("Test complete. Keeping browser open for 15s...")
        print("=" * 60)
        await asyncio.sleep(15.0)

    finally:
        await tool.close()
        print("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
