#!/usr/bin/env python3
"""Headless utility to query Azure OpenAI rate limits and exact TPM/RPM quotas.

Extracts authoritative rate-limit headers directly from Azure OpenAI API responses
for both the configured chat deployment and the embedding deployment, without needing
access to Azure AI Foundry or Azure portal.

Usage:
    cd backend
    python scripts/check_tpm.py
    python scripts/check_tpm.py --json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncAzureOpenAI

from app.core.config import get_settings


async def get_rate_limits() -> dict:
    settings = get_settings()

    client = AsyncAzureOpenAI(
        api_key=settings.azure_openai_api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    )

    results = {}

    # 1. Check Chat Model (e.g., gpt-5-mini)
    try:
        chat_raw = await client.chat.completions.with_raw_response.create(
            model=settings.azure_deployment_name,
            messages=[{"role": "user", "content": "ping"}],
        )
        h = chat_raw.headers
        tpm_limit = int(h.get("x-ratelimit-limit-tokens", 0))
        tpm_remaining = int(h.get("x-ratelimit-remaining-tokens", 0))
        rpm_limit = int(h.get("x-ratelimit-limit-requests", 0))
        rpm_remaining = int(h.get("x-ratelimit-remaining-requests", 0))

        results["chat_model"] = {
            "deployment_name": settings.azure_deployment_name,
            "status": "online",
            "tpm_limit": tpm_limit,
            "tpm_remaining": tpm_remaining,
            "rpm_limit": rpm_limit,
            "rpm_remaining": rpm_remaining,
            "headers": {k: v for k, v in h.items() if "ratelimit" in k.lower()},
        }
    except Exception as exc:
        results["chat_model"] = {
            "deployment_name": settings.azure_deployment_name,
            "status": "error",
            "error": str(exc),
        }

    # 2. Check Embedding Model (e.g., text-embedding-3-small)
    try:
        emb_raw = await client.embeddings.with_raw_response.create(
            model=settings.embedding_model,
            input=["ping"],
        )
        h = emb_raw.headers
        tpm_limit = int(h.get("x-ratelimit-limit-tokens", 0))
        tpm_remaining = int(h.get("x-ratelimit-remaining-tokens", 0))
        rpm_limit = int(h.get("x-ratelimit-limit-requests", 0))
        rpm_remaining = int(h.get("x-ratelimit-remaining-requests", 0))

        results["embedding_model"] = {
            "deployment_name": settings.embedding_model,
            "status": "online",
            "tpm_limit": tpm_limit,
            "tpm_remaining": tpm_remaining,
            "rpm_limit": rpm_limit,
            "rpm_remaining": rpm_remaining,
            "headers": {k: v for k, v in h.items() if "ratelimit" in k.lower()},
        }
    except Exception as exc:
        results["embedding_model"] = {
            "deployment_name": settings.embedding_model,
            "status": "error",
            "error": str(exc),
        }

    return results


async def main():
    parser = argparse.ArgumentParser(
        description="Inspect exact TPM and RPM limits for Azure OpenAI deployments."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print raw JSON format instead of human-readable table.",
    )
    args = parser.parse_args()

    data = await get_rate_limits()

    if args.json_output:
        print(json.dumps(data, indent=2))
        return

    print("\n" + "=" * 65)
    print("      AZURE OPENAI LIVE CAPACITY & RATE LIMIT AUDIT")
    print("=" * 65)

    # Chat Model
    chat = data.get("chat_model", {})
    print(f"\n[1] Chat Model: {chat.get('deployment_name')}")
    if chat.get("status") == "online":
        tpm = chat.get("tpm_limit", 0)
        tpm_rem = chat.get("tpm_remaining", 0)
        rpm = chat.get("rpm_limit", 0)
        rpm_rem = chat.get("rpm_remaining", 0)
        print(
            f"    - Exact TPM Limit     : {tpm:,} tokens/minute ({tpm / 1000:,.0f}k TPM)"
        )
        print(f"    - Remaining Tokens    : {tpm_rem:,} tokens")
        if rpm:
            print(f"    - Exact RPM Limit     : {rpm:,} requests/minute")
            print(f"    - Remaining Requests  : {rpm_rem:,} requests")
    else:
        print(f"    - Error: {chat.get('error')}")

    # Embedding Model
    emb = data.get("embedding_model", {})
    print(f"\n[2] Embedding Model: {emb.get('deployment_name')}")
    if emb.get("status") == "online":
        tpm = emb.get("tpm_limit", 0)
        tpm_rem = emb.get("tpm_remaining", 0)
        rpm = emb.get("rpm_limit", 0)
        rpm_rem = emb.get("rpm_remaining", 0)
        print(
            f"    - Exact TPM Limit     : {tpm:,} tokens/minute ({tpm / 1000:,.0f}k TPM)"
        )
        print(f"    - Remaining Tokens    : {tpm_rem:,} tokens")
        if rpm:
            print(f"    - Exact RPM Limit     : {rpm:,} requests/minute")
            print(f"    - Remaining Requests  : {rpm_rem:,} requests")
    else:
        print(f"    - Error: {emb.get('error')}")

    print("\n" + "=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
