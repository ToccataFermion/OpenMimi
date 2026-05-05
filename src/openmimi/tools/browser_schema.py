"""Pydantic schemas for the BrowserTool input contract.

Design:
- One discriminated union (`BrowserToolInput`) keyed by `action`.
- Locator fields (`target_text`, `target_hint`, `coordinate`) live in a mixin and
  are validated as mutually exclusive: coordinate cannot coexist with semantic targets.
- A single `TypeAdapter` exposes both runtime validation and JSON Schema export.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)


def _coerce_coordinate(value: Any) -> Any:
    """Best-effort coerce common string forms of `[x, y]` into a real list.

    Some Anthropic-compatible upstream providers (e.g. Aliyun MaaS) serialize
    nested JSON values as plain strings inside the `tool_use.input` payload,
    so the model intends `coordinate=[x, y]` but we receive
    `coordinate="[x, y]"`. Without this validator the strict tuple type
    would reject the call and force the model to retry blindly.

    Accepted spellings (whitespace tolerant):
        "[290, 35]"   "(290, 35)"   "290, 35"   "290,35"
        [290, 35]     (290, 35)     # passed through unchanged
    """
    if value is None or isinstance(value, (list, tuple)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (list, tuple)):
            return parsed
        stripped = text.strip("[](){} \t")
        if "," in stripped:
            parts = [p.strip() for p in stripped.split(",")]
            if len(parts) == 2:
                try:
                    return [int(float(parts[0])), int(float(parts[1]))]
                except ValueError:
                    return value
    return value


Coordinate = Annotated[
    tuple[int, int],
    BeforeValidator(_coerce_coordinate),
    Field(description="Pixel [x, y] within the current viewport (origin top-left)."),
]


class ExpectShape(BaseModel):
    """Lightweight post-action expectation used to gate success."""

    model_config = ConfigDict(extra="forbid")

    url_contains: str | None = None
    text_contains: str | list[str] | None = None


class _Base(BaseModel):
    """Common fields shared by every action."""

    model_config = ConfigDict(extra="forbid")

    task_hint: str | None = Field(
        default=None,
        description="Free-form note from the LLM describing intent. Used in audit logs.",
    )
    timeout_s: float | None = Field(
        default=None,
        ge=0.1,
        le=120,
        description="Per-action timeout in seconds; tool may clamp.",
    )
    expect: ExpectShape | None = Field(
        default=None,
        description="Optional success checks evaluated after the action.",
    )


class _LocatorMixin(BaseModel):
    """Locator fields. `coordinate` is mutually exclusive with `target_text`/`target_hint`."""

    target_text: str | None = Field(
        default=None,
        description="Visible text/label of the target (preferred for stable elements).",
    )
    target_hint: str | None = Field(
        default=None,
        description=(
            "Natural language hint, e.g. 'gear icon top right'. "
            "Used when text is unavailable."
        ),
    )
    coordinate: Coordinate | None = None

    @model_validator(mode="after")
    def _check_locator_exclusivity(self) -> Self:
        has_semantic = self.target_text is not None or self.target_hint is not None
        has_coord = self.coordinate is not None
        if has_semantic and has_coord:
            raise ValueError(
                "coordinate is mutually exclusive with target_text/target_hint"
            )
        return self


class NavigateInput(_Base):
    action: Literal["navigate"]
    url: str = Field(min_length=1, description="Absolute URL to navigate to.")


class ClickInput(_Base, _LocatorMixin):
    action: Literal["click"]

    @model_validator(mode="after")
    def _require_locator(self) -> Self:
        if (
            self.target_text is None
            and self.target_hint is None
            and self.coordinate is None
        ):
            raise ValueError(
                "click requires one of: target_text | target_hint | coordinate"
            )
        return self


class HoverInput(_Base, _LocatorMixin):
    action: Literal["hover"]

    @model_validator(mode="after")
    def _require_locator(self) -> Self:
        if (
            self.target_text is None
            and self.target_hint is None
            and self.coordinate is None
        ):
            raise ValueError(
                "hover requires one of: target_text | target_hint | coordinate"
            )
        return self


class TypeInput(_Base, _LocatorMixin):
    action: Literal["type"]
    text: str = Field(description="Text to type. Use a separate `press` action for special keys.")


class PressInput(_Base):
    action: Literal["press"]
    key: str = Field(
        min_length=1,
        description="Key or chord, e.g. 'Enter', 'Tab', 'Escape', 'Ctrl+L'.",
    )


class ScrollInput(_Base):
    action: Literal["scroll"]
    direction: Literal["up", "down", "left", "right"]
    amount: int = Field(ge=1, le=20000, description="Scroll distance in pixels.")
    target_hint: str | None = Field(
        default=None,
        description="Optional region hint; tool may scroll within that region.",
    )


class WaitInput(_Base):
    action: Literal["wait"]
    duration_s: float = Field(gt=0, le=10, description="Wait duration in seconds.")


class ScreenshotInput(_Base):
    action: Literal["screenshot"]


class ExtractInput(_Base):
    action: Literal["extract"]
    instruction: str = Field(
        min_length=1,
        description="Natural-language extraction goal (M1 returns text only).",
    )


class DownloadInput(_Base, _LocatorMixin):
    action: Literal["download"]
    save_as: str | None = Field(
        default=None,
        description="Suggested filename (extension will be enforced from server response).",
    )

    @model_validator(mode="after")
    def _require_locator(self) -> Self:
        if (
            self.target_text is None
            and self.target_hint is None
            and self.coordinate is None
        ):
            raise ValueError(
                "download requires one of: target_text | target_hint | coordinate"
            )
        return self


BrowserToolInput = Annotated[
    NavigateInput
    | ClickInput
    | HoverInput
    | TypeInput
    | PressInput
    | ScrollInput
    | WaitInput
    | ScreenshotInput
    | ExtractInput
    | DownloadInput,
    Field(discriminator="action"),
]

BROWSER_TOOL_INPUT_ADAPTER: TypeAdapter[BrowserToolInput] = TypeAdapter(BrowserToolInput)


class TargetResolved(BaseModel):
    """How the BrowserTool finally located the target (audit/replay only)."""

    model_config = ConfigDict(extra="forbid")

    by: Literal["text", "hint", "coordinate"]
    value: str = Field(description="String form of the locator, e.g. '812,124' for coordinates.")


class DownloadInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str
    source_url: str | None = None
    size_bytes: int | None = None


class BrowserToolDetails(BaseModel):
    """Structured payload placed into ToolResult.details."""

    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    title: str | None = None
    downloads: list[DownloadInfo] = Field(default_factory=list)
    target_resolved: TargetResolved | None = None
    error_code: str | None = None
    retryable: bool | None = None
    attempt: int | None = None


def parse_browser_tool_input(payload: dict[str, Any]) -> BrowserToolInput:
    """Validate a raw tool_use input dict into a typed BrowserToolInput."""
    return BROWSER_TOOL_INPUT_ADAPTER.validate_python(payload)


def browser_tool_input_json_schema() -> dict[str, Any]:
    """JSON Schema dict to feed into Anthropic's `tool.input_schema`."""
    return BROWSER_TOOL_INPUT_ADAPTER.json_schema(
        ref_template="#/$defs/{model}", mode="validation"
    )


__all__ = [
    "BROWSER_TOOL_INPUT_ADAPTER",
    "BrowserToolDetails",
    "BrowserToolInput",
    "ClickInput",
    "DownloadInfo",
    "DownloadInput",
    "ExpectShape",
    "ExtractInput",
    "HoverInput",
    "NavigateInput",
    "PressInput",
    "ScreenshotInput",
    "ScrollInput",
    "TargetResolved",
    "TypeInput",
    "WaitInput",
    "browser_tool_input_json_schema",
    "parse_browser_tool_input",
]
