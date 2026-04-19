"""Unified LLM wrapper — Anthropic or OpenAI, cheap vs premium tier."""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from ada_lead_gen import config

# Token cost tables (USD per 1k tokens, input/output)
_ANTHROPIC_COSTS: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":         (0.00025, 0.00125),
    "claude-haiku-4-5-20251001": (0.00025, 0.00125),
    "claude-sonnet-4-5":        (0.003,   0.015),
    "claude-opus-4-7":          (0.015,   0.075),
}
_OPENAI_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":   (0.00015, 0.0006),
    "gpt-4-turbo":   (0.01,    0.03),
    "gpt-4o":        (0.005,   0.015),
}


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    table = _ANTHROPIC_COSTS if config.LLM_PROVIDER == "anthropic" else _OPENAI_COSTS
    in_rate, out_rate = table.get(model, (0.01, 0.03))
    return (input_tokens / 1000) * in_rate + (output_tokens / 1000) * out_rate


class LLMClient:
    """
    Thin wrapper around Anthropic / OpenAI that enforces:
    - cheap_model for bulk work, premium_model for outreach drafts only
    - JSON-mode responses
    - Automatic retry with exponential backoff
    - Per-call cost logging to SQLite
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.provider = config.LLM_PROVIDER
        self.cheap_model = config.CHEAP_MODEL
        self.premium_model = config.PREMIUM_MODEL
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            if self.provider == "anthropic":
                import anthropic
                self._client = anthropic.Anthropic(api_key=config.__dict__.get("ANTHROPIC_API_KEY") or __import__("os").getenv("ANTHROPIC_API_KEY"))
            else:
                import openai
                self._client = openai.OpenAI(api_key=__import__("os").getenv("OPENAI_API_KEY"))
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
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
        """
        model = self.premium_model if use_premium else self.cheap_model

        # Enforce cost guardrail
        from ada_lead_gen.db import get_run_spend, log_llm_call
        current_spend = get_run_spend(self.run_id)
        if current_spend >= config.MAX_RUN_COST_USD:
            raise RuntimeError(
                f"Cost guardrail hit: ${current_spend:.4f} >= ${config.MAX_RUN_COST_USD}"
            )

        client = self._get_client()
        input_tokens = output_tokens = 0
        raw_text = ""

        if self.provider == "anthropic":
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = resp.content[0].text
            input_tokens = resp.usage.input_tokens
            output_tokens = resp.usage.output_tokens

        else:
            resp = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            raw_text = resp.choices[0].message.content or ""
            input_tokens = resp.usage.prompt_tokens
            output_tokens = resp.usage.completion_tokens

        cost = _compute_cost(model, input_tokens, output_tokens)
        log_llm_call(self.run_id, self.provider, model, purpose, input_tokens, output_tokens, cost)

        logger.debug(
            "LLM [{}] {} in={} out={} ${:.5f}",
            purpose, model, input_tokens, output_tokens, cost,
        )

        # Extract JSON — handle markdown code fences
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("JSON parse failed for {}: {} | raw: {!r}", purpose, exc, raw_text[:200])
            raise

    def get_run_spend(self) -> float:
        """Return total spend for this run."""
        from ada_lead_gen.db import get_run_spend
        return get_run_spend(self.run_id)
