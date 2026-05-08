"""Test if the configured model supports vision."""
from __future__ import annotations

import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openmimi.llm import AnthropicClient

# Valid 10x10 red PNG
RED_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAIAAAACUFjqAAAAE0lEQVR4nGP8z4APMOGVZRip0gBBLAETee26JgAAAABJRU5ErkJggg=="


async def main() -> None:
    model = os.environ.get("ANTHROPIC_MODEL", "qwen-vl-plus")
    print(f"Testing model: {model}")

    client = AnthropicClient(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model=model,
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
        enable_prompt_caching=False,
    )
    try:
        result = await client.create(
            system="test",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "What color is this image? Answer in one word."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": RED_PNG_B64}}
            ]}],
            tools=[],
            max_tokens=100,
        )
        print("Success:", result.get("stop_reason"))
        for block in result.get("content", []):
            if block.get("type") == "text":
                print("Text:", block.get("text"))
    except Exception as e:
        print("Error:", type(e).__name__, e)


if __name__ == "__main__":
    asyncio.run(main())
