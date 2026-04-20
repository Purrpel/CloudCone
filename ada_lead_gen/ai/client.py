"""Unified LLM wrapper — Anthropic or OpenAI, cheap vs premium tier."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ada_lead_gen import config
from ada_lead_gen.db import get_run_spend, log_llm_call

# Token cost tables (USD per 1k tokens, input/output)
_ANTHROPIC_COSTS: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":          (0.00025, 0.00125),
    "claude-haiku-4-5-20251001": (0.00025, 0.00125),
    "claude-sonnet-4-5":         (0.003,   0.015),
    "claude-sonnet-4-6":         (0.003,   0.015),
    "claude-opus-4-6":           (0.015,   0.075),
    "claude-opus-4-7":           (0.015,   0.075),
}
_OPENAI_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":   (0.00015, 0.0006),
    "gpt-4-turbo":   (0.01,    0.03),
    "gpt-4o":        (0.005,   0.015),
}

# Safe defaults — prefer cheap-tier rates for unknown models so the guardrail
# isn't tripped by phantom spend. Warning is logged once per unknown model.
_UNKNOWN_MODEL_RATES = (0.001, 0.005)
_warned_models: set[str] = set()


class CostGuardrailError(RuntimeError):
    """Raised when projected spend exceeds MAX_RUN_COST_USD. Never retried."""


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    table = _ANTHROPIC_COSTS if config.LLM_PROVIDER == "anthropic" else _OPENAI_COSTS
    rates = table.get(model)
    if rates is None:
        if model not in _warned_models:
            logger.warning("Unknown model '{}' — using conservative default rates", model)
            _warned_models.add(model)
        rates = _UNKNOWN_MODEL_RATES
    in_rate, out_rate = rates
    return (input_tokens / 1000) * in_rate + (output_tokens / 1000) * out_rate


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences if present, return inner JSON."""
    text = text.strip()
    if text.startswith("```"):
        # Match ```json ... ``` or ``` ... ```
        match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
        if match:
            return match.group(1).strip()
    return text


class LLMClient:
    """
    Thin wrapper around Anthropic / OpenAI that enforces:
    - cheap_model for bulk work, premium_model for outreach drafts only
    - JSON-mode responses
    - Retry only on transient errors (network, rate limit) — never cost guardrail
    - Per-call cost logging to SQLite
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.provider = config.LLM_PROVIDER
        self.cheap_model = config.CHEAP_MODEL
        self.premium_model = config.PREMIUM_MODEL

        if self.provider not in ("anthropic", "openai"):
            raise ValueError(f"Unsupported LLM_PROVIDER: {self.provider!r}")

        # Validate API key at init — fail fast instead of crashing mid-run
        key_env = "ANTHROPIC_API_KEY" if self.provider == "anthropic" else "OPENAI_API_KEY"
        if not os.getenv(key_env):
            raise ValueError(f"{key_env} is not set in env")

        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            if self.provider == "anthropic":
                import anthropic
                self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            else:
                import openai
                self._client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return self._client

    def _send(self, model: str, system: str, prompt: str) -> tuple[str, int, int]:
        """Low-level send that is retried on transient errors only."""
        client = self._get_client()

        if self.provider == "anthropic":
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text, resp.usage.input_tokens, resp.usage.output_tokens

        resp = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return (
            resp.choices[0].message.content or "",
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
        )

    def call(
        self,
        prompt: str,
        purpose: str,
        use_premium: bool = False,
        system: str = "You are a helpful assistant. Always respond with valid JSON.",
    ) -> dict[str, Any]:
        """
        Send a prompt, get back a parsed JSON dict.

        Args:
            prompt: User message content.
            purpose: Label for cost tracking (e.g. 'classify', 'score').
            use_premium: If True, use premium_model. Only for outreach drafts.
            system: System message.

        Raises:
            CostGuardrailError if MAX_RUN_COST_USD is hit. Never retried.
        """
        # Cost guardrail — checked before any network call
        current_spend = get_run_spend(self.run_id)
        if current_spend >= config.MAX_RUN_COST_USD:
            raise CostGuardrailError(
                f"Cost guardrail hit: ${current_spend:.4f} >= ${config.MAX_RUN_COST_USD}"
            )

        model = self.premium_model if use_premium else self.cheap_model

        # Retry transient API errors only. JSON parsing errors do NOT retry.
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type((ConnectionError, TimeoutError, IOError)),
            reraise=True,
        )
        def _do_send() -> tuple[str, int, int]:
            return self._send(model, system, prompt)

        try:
            raw_text, input_tokens, output_tokens = _do_send()
        except Exception as exc:
            logger.error("LLM call failed for {}: {}", purpose, exc)
            raise

        cost = _compute_cost(model, input_tokens, output_tokens)
        log_llm_call(self.run_id, self.provider, model, purpose, input_tokens, output_tokens, cost)

        logger.debug(
            "LLM [{}] {} in={} out={} ${:.5f}",
            purpose, model, input_tokens, output_tokens, cost,
        )

        text = _strip_json_fences(raw_text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "JSON parse failed for {}: {} | raw: {!r}",
                purpose, exc, raw_text[:200],
            )
            # Return empty dict rather than raising — the caller treats missing
            # keys as defaults, so the pipeline continues instead of dying
            return {}

    def get_run_spend(self) -> float:
        """Return total spend for this run."""
        return get_run_spend(self.run_id)
