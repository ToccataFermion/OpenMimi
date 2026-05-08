"""Solve CAPTCHA using LLM vision to estimate gap position."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.tools.agent_browser import AgentBrowserTool
from openmimi.tools.computer import ComputerTool
from openmimi.llm.anthropic_client import AnthropicClient


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def analyze_captcha_with_vision(screenshot_path: str) -> dict:
    """Send CAPTCHA screenshot to vision model and get gap estimate."""
    api_key = os.environ.get("OPENMIMI_LLM_API_KEY", "")
    base_url = os.environ.get("OPENMIMI_LLM_BASE_URL", "")
    model = os.environ.get("OPENMIMI_LLM_MODEL", "qwen-vl-plus")

    client = AnthropicClient(
        api_key=api_key or None,
        model=model,
        base_url=base_url or None,
        enable_prompt_caching=False,
        request_timeout_s=60.0,
    )

    with open(screenshot_path, "rb") as f:
        screenshot_b64 = base64.b64encode(f.read()).decode()

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": screenshot_b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        "This is a slider CAPTCHA screenshot. I need you to estimate the horizontal "
                        "pixel distance from the puzzle piece (on the left) to the gap (on the right).\n\n"
                        "The background image is 340 pixels wide. The puzzle piece starts at the left edge.\n"
                        "Estimate how many pixels the puzzle piece needs to move RIGHT to align with the gap.\n\n"
                        "Respond with ONLY a JSON object in this exact format:\n"
                        '{"estimated_gap_px": <number>, "confidence": "high|medium|low", "reasoning": "<brief explanation>"}'
                    ),
                },
            ],
        }
    ]

    log("[vision] Sending screenshot to LLM for gap analysis...")
    response = await client.create(
        system="You are a precise visual analyzer. Estimate pixel distances from images.",
        messages=messages,
        tools=[],
        max_tokens=512,
    )

    content = response.get("content", [])
    text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
    if not text_blocks:
        raise ValueError("No text response from vision model")

    text = text_blocks[0].get("text", "")
    log(f"[vision] Raw response: {text}")

    # Extract JSON from response
    try:
        # Find JSON object in response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
        else:
            data = json.loads(text)
    except json.JSONDecodeError:
        log(f"[vision] Failed to parse JSON from: {text}")
        data = {"estimated_gap_px": 150, "confidence": "low", "reasoning": "parse failed"}

    log(f"[vision] Parsed: {json.dumps(data)}")
    return data


async def main() -> None:
    download_dir = tempfile.mkdtemp(prefix="openmimi_ab_")
    browser = AgentBrowserTool(
        download_dir=download_dir,
        viewport=(1280, 800),
        headless=False,
        browser_args=["--disable-blink-features=AutomationControlled"],
    )
    computer = ComputerTool()

    try:
        log("=== Login ===")
        await browser({"action": "navigate", "url": "https://xft.cmbchina.com/"})
        await asyncio.sleep(3.0)
        await browser({"action": "click", "target_text": "登录"})
        await asyncio.sleep(2.0)
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const inputs = Array.from(document.querySelectorAll('input.ant-input'));
                    const phone = inputs.find(el => el.type === 'text');
                    const pass = inputs.find(el => el.type === 'password');
                    const checkbox = document.querySelector('input.ant-checkbox-input');
                    function setReactValue(element, value) {
                        const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        valueSetter.call(element, value);
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    if (phone) setReactValue(phone, '18584828398');
                    if (pass) setReactValue(pass, 'Liszt123');
                    if (checkbox && !checkbox.checked) checkbox.click();
                    return {ok: true};
                })()
            """,
        })
        await asyncio.sleep(0.5)
        await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.PasswordLogin_loginBtn__yuCsm');
                    if (btn) { btn.click(); return {clicked: true}; }
                    return {clicked: false};
                })()
            """,
        })
        await asyncio.sleep(4.0)

        # Take screenshot
        screenshot_path = os.path.join(download_dir, "captcha_for_vision.png")
        result = await browser({
            "action": "screenshot",
            "path": screenshot_path,
        })
        log(f"Screenshot saved: {screenshot_path}")

        # Get handle position
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const r = btn.getBoundingClientRect();
                    const sx = window.screenX + (window.outerWidth - window.innerWidth) / 2;
                    const sy = window.screenY + window.outerHeight - window.innerHeight - (window.outerWidth - window.innerWidth) / 2;
                    return {
                        screenX: Math.round(sx + r.left + r.width/2),
                        screenY: Math.round(sy + r.top + r.height/2),
                    };
                })()
            """,
        })
        pos = json.loads(result.output or "{}")
        sx, sy = pos["screenX"], pos["screenY"]
        log(f"Handle screen: ({sx}, {sy})")

        # Ask vision model for gap estimate
        vision_result = await analyze_captcha_with_vision(screenshot_path)
        gap_px = vision_result.get("estimated_gap_px", 150)
        confidence = vision_result.get("confidence", "low")
        log(f"[vision] Estimated gap: {gap_px}px (confidence: {confidence})")

        # Convert puzzle gap to handle drag distance
        # Handle range: 340 - 60 = 280px
        # Puzzle range: 340 - 78 = 262px
        # handle_drag = gap_px * 280 / 262
        handle_drag = int(gap_px * 280 / 262)
        log(f"[vision] Handle drag distance: {handle_drag}px")

        # Perform drag
        log(f"\n=== Dragging {handle_drag}px ===")
        await browser({"action": "focus"})
        await asyncio.sleep(0.3)
        await computer({
            "action": "mouse_drag",
            "x": sx, "y": sy,
            "end_x": sx + handle_drag, "end_y": sy,
            "steps": 80, "delay_ms": 25,
        })

        # Check result
        await asyncio.sleep(2.0)
        result = await browser({
            "action": "eval",
            "js": """
                (() => {
                    const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                    const btn = document.querySelector('.imageVerifyDragButton');
                    const drag = document.querySelector('.dragImage');
                    return {
                        hasVerify: !!verify,
                        btnLeft: btn ? window.getComputedStyle(btn).left : null,
                        dragLeft: drag ? window.getComputedStyle(drag).left : null,
                    };
                })()
            """,
        })
        state = json.loads(result.output or "{}")
        log(f"State after drag: {json.dumps(state)}")

        if not state.get("hasVerify"):
            log("SUCCESS! CAPTCHA solved!")
        else:
            log("CAPTCHA still present. Trying nearby distances...")
            # Try +/- 10px
            for offset in [-10, 10, -20, 20]:
                try_drag = handle_drag + offset
                if try_drag < 0 or try_drag > 280:
                    continue
                log(f"\n--- Trying offset {offset}: {try_drag}px ---")
                await browser({"action": "focus"})
                await asyncio.sleep(0.3)
                await computer({
                    "action": "mouse_drag",
                    "x": sx, "y": sy,
                    "end_x": sx + try_drag, "end_y": sy,
                    "steps": 80, "delay_ms": 25,
                })
                await asyncio.sleep(2.0)
                result = await browser({
                    "action": "eval",
                    "js": """
                        (() => {
                            const verify = document.querySelector('.xftImageVerify') || document.querySelector('.imageVerify');
                            return {hasVerify: !!verify};
                        })()
                    """,
                })
                state = json.loads(result.output or "{}")
                if not state.get("hasVerify"):
                    log(f"SUCCESS at {try_drag}px!")
                    break
                log(f"  Still present, resetting...")
                await asyncio.sleep(1.0)
            else:
                log("All attempts failed.")

        log("\nDone.")
        await asyncio.sleep(3.0)

    finally:
        await browser.close()
        log("Browser closed.")


if __name__ == "__main__":
    asyncio.run(main())
