"""Pydantic v2 schema for eval case YAML files.

Extends the CVX schema additively for CMS Tier 3 (REST + WebSocket).
CVX's existing 'text' and 'audio' Input types are preserved unchanged.
"""

from typing import Any, Literal

from pydantic import BaseModel, model_validator


class Input(BaseModel):
    """Input data for an eval case.

    Attributes:
        type: 'text' or 'audio' (CVX); 'rest' or 'websocket' (CMS Tier 3 additions).
        utterance: Natural language input (for text-input cases).
        audio_path: Relative path to a WAV file in evals/audio/ (for audio-input cases).
        input_args: Structured kwargs for the tool (used by Tier 1 multi-parameter tools).
        method: HTTP method (CMS Tier 3 REST).
        path: URL path (CMS Tier 3 REST + WebSocket).
        path_params: Path parameter substitutions (CMS Tier 3 REST).
        query_params: Query string parameters (CMS Tier 3 REST).
        body: Request body (CMS Tier 3 REST).
        subscribe: WebSocket subscription payload (CMS Tier 3 WebSocket).
        duration_ms: How long to capture WebSocket events (CMS Tier 3 WebSocket).
    """

    type: Literal["text", "audio", "rest", "websocket"]
    utterance: str | None = None
    audio_path: str | None = None
    input_args: dict[str, Any] | None = None
    # CMS Tier 3 extensions (REST):
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"] | None = None
    path: str | None = None
    path_params: dict[str, str] | None = None
    query_params: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    # CMS Tier 3 extensions (WebSocket):
    subscribe: dict[str, Any] | None = None
    duration_ms: int | None = None

    @model_validator(mode="after")
    def require_utterance_or_audio(self) -> "Input":
        if self.type in ("text", "audio"):
            if self.utterance is None and self.audio_path is None and self.input_args is None:
                raise ValueError("Input requires at least one of: utterance, audio_path, input_args")
        elif self.type == "rest":
            if not (self.method and self.path):
                raise ValueError("REST input requires method and path")
        elif self.type == "websocket":
            if not (self.subscribe and self.duration_ms):
                raise ValueError("WebSocket input requires subscribe and duration_ms")
        return self


class Expected(BaseModel):
    """Expected outputs and assertions for an eval case.

    Attributes:
        tool_output: Expected return value of a tool (Tier 1 only).
        tool_calls: List of expected tool invocations (Tier 2/3 CVX-style).
        routing: Routing decision assertions.
        response: Response assertions.
        status_code: Expected HTTP status code (CMS Tier 3 REST).
        events: WebSocket event assertions (CMS Tier 3 WebSocket).
    """

    tool_output: dict[str, Any] | None = None
    tool_calls: list[Any] | None = None
    routing: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    # CMS Tier 3 extensions:
    status_code: int | None = None
    events: dict[str, Any] | None = None


class EvalCase(BaseModel):
    """A single eval case (golden test).

    Attributes:
        id: Unique case identifier (kebab-case).
        description: Human-readable description of what the case tests.
        tier: Evaluation tier (1=unit, 2=conversation, 3=e2e).
        persona: Agent/user persona (e.g., 'fleet-operator').
        vehicle_id: Optional vehicle identifier for context.
        driver_id: Optional driver identifier for context.
        input: Input data.
        expected: Expected outputs and assertions.
        latency_budget_ms: Maximum allowed latency in milliseconds.
    """

    id: str
    description: str
    tier: Literal[1, 2, 3]
    persona: str
    vehicle_id: str | None = None
    driver_id: str | None = None
    input: Input
    expected: Expected
    latency_budget_ms: int | None = None

    @model_validator(mode="after")
    def validate_tier_fields(self) -> "EvalCase":
        if self.tier == 1:
            if self.expected.tool_output is None:
                raise ValueError("Tier 1 cases require expected.tool_output")
        elif self.tier in (2, 3):
            if self.tier == 3 and (
                self.expected.status_code is not None or self.expected.events is not None
            ):
                # CMS-style Tier 3 case (REST or WebSocket)
                if self.input.type == "rest":
                    if not (self.input.method and self.input.path):
                        raise ValueError("Tier 3 REST cases require input.method and input.path")
                elif self.input.type == "websocket":
                    if not (self.input.subscribe and self.input.duration_ms):
                        raise ValueError(
                            "Tier 3 WebSocket cases require input.subscribe and input.duration_ms"
                        )
                else:
                    raise ValueError(
                        f"Tier 3 input.type must be 'rest' or 'websocket' for CMS; "
                        f"got {self.input.type!r}"
                    )
            else:
                # CVX-style Tier 2/3 case
                if self.expected.tool_calls is None or self.expected.response is None:
                    raise ValueError(
                        "Tier 2/3 cases require both expected.tool_calls and expected.response"
                    )
        return self
