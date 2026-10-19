"""Pure configuration-driven token costing and pricing math engine (Subtask 2.4.4).

Provides exact cost calculations across the 5 platform stages:
- Query text embedding
- Guardrail / groundedness answer embedding
- Ingestion document chunk embedding
- LLM input prompt tokens (including cached inputs)
- LLM output completion tokens (reasoning + visible)

Uses Decimal representation internally to eliminate floating-point drift and
snapshots the applied pricing rates inside each exchange record.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from app.model_settings.service import current_model_settings


class RateCard(Protocol):
    """USD per 1M tokens: `Settings` or the effective Settings > Models values."""

    price_embed_per_million: float
    price_prompt_per_million: float
    price_output_per_million: float
    price_cached_per_million: float


def get_current_pricing_rates() -> dict[str, Decimal]:
    """Active rates (Settings > Models overrides over .env) as Decimal per 1M tokens."""
    rates = current_model_settings()
    return {
        "embed_per_m": Decimal(str(rates.price_embed_per_million)),
        "prompt_per_m": Decimal(str(rates.price_prompt_per_million)),
        "output_per_m": Decimal(str(rates.price_output_per_million)),
        "cached_per_m": Decimal(str(rates.price_cached_per_million)),
    }


def compute_exchange_cost(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
    cached_tokens: int = 0,
    query_embed_tokens: int = 0,
    guardrail_embed_tokens: int = 0,
    pricing_snapshot: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any]]:
    """Compute total cost in USD and return (cost_usd, token_details_dict).

    Reasoning tokens are billed as output tokens per Azure OpenAI billing model.
    """
    if pricing_snapshot:
        rates = {k: Decimal(str(v)) for k, v in pricing_snapshot.items()}
    else:
        rates = get_current_pricing_rates()

    per_m = Decimal("1000000")

    # 1. Embeddings (query + guardrail)
    cost_query_embed = (Decimal(query_embed_tokens) * rates["embed_per_m"]) / per_m
    cost_guardrail_embed = (
        Decimal(guardrail_embed_tokens) * rates["embed_per_m"]
    ) / per_m
    cost_total_embed = cost_query_embed + cost_guardrail_embed

    # 2. LLM Inputs
    uncached_prompt = max(0, prompt_tokens - cached_tokens)
    cost_prompt = (Decimal(uncached_prompt) * rates["prompt_per_m"]) / per_m
    cost_cached = (
        Decimal(cached_tokens) * rates.get("cached_per_m", rates["prompt_per_m"])
    ) / per_m

    # 3. LLM Outputs (Visible + Reasoning)
    visible_tokens = max(0, completion_tokens - reasoning_tokens)
    cost_reasoning = (Decimal(reasoning_tokens) * rates["output_per_m"]) / per_m
    cost_visible = (Decimal(visible_tokens) * rates["output_per_m"]) / per_m
    cost_total_output = cost_reasoning + cost_visible

    # Grand Total
    total_cost_dec = cost_total_embed + cost_prompt + cost_cached + cost_total_output
    total_cost_float = float(
        total_cost_dec.quantize(Decimal("0.0000001"), rounding=ROUND_HALF_UP)
    )

    # Serialisable token details with pricing snapshot
    details = {
        "pricing": {k: float(v) for k, v in rates.items()},
        "tokens": {
            "query_embed": query_embed_tokens,
            "guardrail_embed": guardrail_embed_tokens,
            "total_embed": query_embed_tokens + guardrail_embed_tokens,
            "prompt": prompt_tokens,
            "cached": cached_tokens,
            "completion": completion_tokens,
            "reasoning": reasoning_tokens,
            "visible": visible_tokens,
            "total_llm": prompt_tokens + completion_tokens,
            "total_billed": (
                query_embed_tokens
                + guardrail_embed_tokens
                + prompt_tokens
                + completion_tokens
            ),
        },
        "costs_usd": {
            "query_embed": float(cost_query_embed),
            "guardrail_embed": float(cost_guardrail_embed),
            "prompt": float(cost_prompt + cost_cached),
            "reasoning": float(cost_reasoning),
            "visible": float(cost_visible),
            "total": total_cost_float,
        },
    }

    return total_cost_float, details


def calculate_stage_costs(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
    cached_tokens: int = 0,
    query_embed_tokens: int = 0,
    guardrail_embed_tokens: int = 0,
    rates: dict[str, Decimal | float] | None = None,
) -> dict[str, float]:
    """Computes stage-by-stage token costs with 6 decimal places of precision."""
    _total_cost, details = compute_exchange_cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        query_embed_tokens=query_embed_tokens,
        guardrail_embed_tokens=guardrail_embed_tokens,
        pricing_snapshot=rates,
    )
    return details["costs_usd"]
