"""
Query /v1/models on OpenAI and Anthropic to discover what a given API key
can access. Used by the Settings page (display) and the index page (to
filter the model picker to only runnable models).
"""

from __future__ import annotations

import httpx


def fetch_openai(api_key: str) -> list[str]:
    """Return all model IDs visible to this OpenAI API key. Empty on error."""
    try:
        r = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    return sorted(m["id"] for m in r.json().get("data", []))


def fetch_anthropic(api_key: str) -> list[str]:
    """Return all model IDs visible to this Anthropic API key. Empty on error."""
    try:
        r = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=10,
        )
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    return sorted(m["id"] for m in r.json().get("data", []))
