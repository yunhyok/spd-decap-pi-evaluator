"""Optional synchronous client for OpenAI-compatible and Ollama local models."""

from __future__ import annotations

import json
import re
from ipaddress import ip_address
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .contracts import (
    AIGroundingError,
    AnalysisReport,
    AssistantSource,
    PlotFeatures,
    validate_analysis_report,
)


class LocalLLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = "http://127.0.0.1:11434"
    model: str = Field(min_length=1)
    api_kind: str = Field(default="ollama", pattern=r"^(ollama|openai)$")
    endpoint: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    connect_timeout_s: float = Field(default=10.0, gt=0.0, le=60.0)
    read_timeout_s: float = Field(default=120.0, gt=0.0, le=300.0)
    write_timeout_s: float = Field(default=30.0, gt=0.0, le=120.0)
    max_output_tokens: int = Field(default=1_024, ge=64, le=4_096)
    seed: int = 0
    allow_remote: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        _endpoint_hostname(value)
        return value.rstrip("/")

    def model_post_init(self, __context: Any) -> None:
        if not self.allow_remote and local_llm_endpoint_requires_remote_access(
            self.base_url
        ):
            raise ValueError(
                "non-loopback endpoints require allow_remote=True explicitly"
            )


def _endpoint_hostname(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint URL must be an http(s) URL with a host")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("endpoint URL contains an invalid port") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint URL must not contain embedded credentials")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("endpoint URL must not contain params, query, or fragment")
    return parsed.hostname.casefold()


def local_llm_endpoint_requires_remote_access(base_url: str) -> bool:
    """Return whether an endpoint leaves literal loopback address space.

    Hostnames are deliberately not DNS-resolved here: a name other than the
    exact ``localhost`` token requires explicit consent, which avoids silently
    trusting DNS or search-suffix changes.
    """

    hostname = _endpoint_hostname(base_url)
    if hostname == "localhost":
        return False
    try:
        return not ip_address(hostname).is_loopback
    except ValueError:
        return True


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class LocalLLMClient:
    """Typed local-LLM adapter with fail-closed deterministic fallbacks."""

    def __init__(
        self,
        config: LocalLLMConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        headers = {"Accept": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=config.connect_timeout_s,
                read=config.read_timeout_s,
                write=config.write_timeout_s,
                pool=config.connect_timeout_s,
            ),
            transport=transport,
            headers=headers,
            # A literal loopback URL is part of the data-boundary decision.
            # Inheriting HTTP_PROXY/HTTPS_PROXY could silently send that request
            # off-machine, so every configured endpoint is contacted directly.
            trust_env=False,
        )

    def __enter__(self) -> "LocalLLMClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def analyze_plot(self, features: PlotFeatures) -> AnalysisReport:
        try:
            report = self._request(
                payload=features,
                response_model=AnalysisReport,
                task=(
                    "Explain only the supplied numeric feature evidence. Every finding "
                    "must cite one or more supplied evidence_ids. Do not infer a proven "
                    "root cause or request unstructured visual input."
                ),
            ).model_copy(
                update={
                    "source": AssistantSource.LOCAL_LLM,
                    "fallback_reason": None,
                }
            )
            return validate_analysis_report(report, features)
        except Exception as exc:  # deterministic fail-closed boundary
            return self._analysis_fallback(features, _failure_category(exc))

    def _request(
        self,
        *,
        payload: BaseModel,
        response_model: type[ResponseModel],
        task: str,
    ) -> ResponseModel:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a constrained PDN analysis component. Respond with one JSON "
                    "object matching the provided schema. "
                    + task
                ),
            },
            {"role": "user", "content": payload.model_dump_json()},
        ]
        schema = response_model.model_json_schema()
        schema_json = json.dumps(
            schema,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        messages[0]["content"] = (
            "You are a constrained PDN analysis component. Return exactly one raw "
            "JSON object with no Markdown code fences or commentary. The object must "
            "validate against this exact JSON Schema: "
            f"{schema_json}. Copy request identifiers exactly. {task}"
        )
        if self.config.api_kind == "openai":
            body: dict[str, Any] = {
                "model": self.config.model,
                "temperature": 0,
                "seed": self.config.seed,
                "stream": False,
                "max_tokens": self.config.max_output_tokens,
                "messages": messages,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_model.__name__,
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
            default_endpoint = "/v1/chat/completions"
        else:
            body = {
                "model": self.config.model,
                "stream": False,
                "format": schema,
                "options": {
                    "temperature": 0,
                    "seed": self.config.seed,
                    "num_predict": self.config.max_output_tokens,
                },
                "messages": messages,
            }
            default_endpoint = "/api/chat"

        response = self._client.post(
            self._endpoint_url(self.config.endpoint or default_endpoint), json=body
        )
        response.raise_for_status()
        envelope = response.json()
        content = _response_content(envelope, self.config.api_kind)
        decoded = _decode_json_object(content)
        return response_model.model_validate(decoded)

    def _endpoint_url(self, endpoint: str) -> str:
        endpoint = "/" + endpoint.lstrip("/")
        if self.config.base_url.endswith("/v1") and endpoint.startswith("/v1/"):
            endpoint = endpoint[3:]
        return self.config.base_url + endpoint

    @staticmethod
    def _analysis_fallback(features: PlotFeatures, reason: str) -> AnalysisReport:
        return AnalysisReport(
            analysis_id=features.analysis_id,
            source=AssistantSource.DETERMINISTIC_FALLBACK,
            findings=(),
            fallback_reason=reason,
        )


def _response_content(envelope: Any, api_kind: str) -> str:
    try:
        if api_kind == "openai":
            content = envelope["choices"][0]["message"]["content"]
            if isinstance(content, list):
                text_parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
                ]
                content = "".join(text_parts)
        else:
            content = envelope["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("local LLM response envelope is malformed") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("local LLM response content is empty")
    return content


_JSON_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE,
)


def _decode_json_object(content: str) -> dict[str, Any]:
    """Decode one JSON object, tolerating only an exact JSON Markdown fence.

    Some OpenAI-compatible local servers return a fenced object even when
    ``response_format=json_schema`` is requested.  Accepting only a full-string
    JSON fence keeps prose, multiple objects, and arbitrary substring recovery
    fail-closed; Pydantic still performs the strict contract validation.
    """

    stripped = content.strip()
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        match = _JSON_FENCE.fullmatch(stripped)
        if match is None:
            raise
        decoded = json.loads(match.group("body").strip())
    if not isinstance(decoded, dict):
        raise ValueError("local LLM response must contain one JSON object")
    return decoded


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, AIGroundingError):
        return "grounding_validation_failed"
    if isinstance(exc, (ValidationError, ValueError, KeyError, json.JSONDecodeError)):
        return "response_schema_validation_failed"
    if isinstance(exc, httpx.ConnectTimeout):
        return "connection_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "inference_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "request_write_timeout"
    if isinstance(exc, httpx.PoolTimeout):
        return "connection_pool_timeout"
    if isinstance(exc, httpx.TimeoutException):
        return "request_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connection_failed"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_status_failed"
    if isinstance(exc, httpx.HTTPError):
        return "http_transport_failed"
    return "local_ai_unavailable"
