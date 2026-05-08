"""Test xft login with working eval - prints to stderr for capture."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    tool = AgentBrowserTool(download_dir=download_dir, viewport=(1280, 800))

    try:
        log("Step 1: Navigate")
        await tool({"action": "navigate", "url": "https://xft.cmbchina.com/"})

        log("Step 2: Click login")
        await tool({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)

        log("Step 3: Inspect DOM for editable elements")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const editable = all.filter(el => {
                        const t = el.tagName;
                        const r = el.getAttribute('role');
                        return t === 'INPUT' || t === 'TEXTAREA' || r === 'textbox' || el.isContentEditable;
                    });
                    return editable.map(el => ({
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        class: el.className?.slice(0, 40),
                        type: el.type,
                        placeholder: el.getAttribute('placeholder')?.slice(0, 20),
                        text: el.textContent?.slice(0, 20)
                    }));
                })()
            """,
        })
        log(f"  Editable elements: {result.output}")

        log("Step 4: Deep inspect textboxes")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const textboxes = all.filter(el => el.getAttribute('role') === 'textbox');
                    return textboxes.map((el, i) => ({
                        index: i,
                        tag: el.tagName,
                        class: el.className?.slice(0, 60),
                        id: el.id,
                        name: el.getAttribute('name'),
                        contenteditable: el.contentEditable,
                        isContentEditable: el.isContentEditable,
                        innerHTML: el.innerHTML?.slice(0, 200),
                        outerHTML: el.outerHTML?.slice(0, 300),
                        parentTag: el.parentElement?.tagName,
                        parentClass: el.parentElement?.className?.slice(0, 40)
                    }));
                })()
            """,
        })
        log(f"  Textbox details: {result.output}")

        log("Step 5: Try native value setter approach")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const textboxes = all.filter(el => el.getAttribute('role') === 'textbox');
                    const phone = textboxes[0];
                    const pass = textboxes[1];
                    function setValue(el, val) {
                        if (!el) return false;
                        const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement?.prototype || {}, 'value');
                        if (d && d.set) {
                            d.set.call(el, val);
                        } else {
                            el.value = val;
                        }
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return true;
                    }
                    setValue(phone, '18584828398');
                    setValue(pass, 'Liszt123');
                    return {phoneValue: phone?.value, phoneText: phone?.textContent, passValue: pass?.value, passText: pass?.textContent};
                })()
            """,
        })
        log(f"  Native setter result: {result.output}")

        log("Step 6: Find and inspect the login button")
        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const all = Array.from(document.querySelectorAll('*'));
                    const btns = all.filter(el => {
                        const t = el.tagName;
                        const r = el.getAttribute('role');
                        const txt = el.textContent?.trim();
                        return t === 'BUTTON' || r === 'button' || txt === '登录' || txt === '密码登录';
                    });
                    return btns.map(el => ({
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        class: el.className?.slice(0, 60),
                        text: el.textContent?.trim()?.slice(0, 30),
                        type: el.type,
                        disabled: el.disabled,
                        onclick: !!el.onclick,
                        listeners: !!el._reactListeners
                    }));
                })()
            """,
        })
        log(f"  Buttons: {result.output}")

        log("Step 7: Check agreement and try clicking login via eval")
        await tool({"action": "check", "ref": "@e20"})

        result = await tool({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('[class*="login"], [class*="submit"]');
                    if (btn) {
                        btn.focus();
                        btn.click();
                        btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                        return {clicked: true, tag: btn.tagName, class: btn.className?.slice(0, 40)};
                    }
                    return {clicked: false};
                })()
            """,
        })
        log(f"  Eval click result: {result.output}")
        await asyncio.sleep(3.0)

        log("Step 8: Snapshot")
        result = await tool({"action": "snapshot"})
        has_login = "密码登录" in (result.output or "")
        log(f"  Login dialog still visible: {has_login}")

        log("\nKeeping browser open for 5s...")
        await asyncio.sleep(5.0)

    finally:
        await tool.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
