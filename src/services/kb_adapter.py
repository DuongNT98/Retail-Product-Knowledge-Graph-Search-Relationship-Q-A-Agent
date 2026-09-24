"""AgentCore Platform v1.0 - RET-C2-566 product-KG KB adapter (production wiring).

Addresses the post-release audit finding POST-02 (production data source):
the deployable endpoint must obtain the relationship-node KB and the SKU KB
from a configured, authenticated source instead of running against empty
in-memory lists (which returned a false-negative "No matching SKUs found"
success for the advertised query).

Contract:
  - `build_kb_client(secrets, *, allow_stub=False)` returns an object exposing
    `relationship_records() -> list[dict]` and `sku_records() -> list[dict]`
    (the two lists the main node's deterministic multi-hop lookup consumes).
  - Production (`allow_stub=False`): `PRODUCT_KG_API_KEY` is MANDATORY; a
    missing/empty key raises `KBConfigError` at construction (the endpoint must
    not silently serve empty KBs). The base URL comes from `PRODUCT_KG_BASE_URL`.
  - Demo/test (`allow_stub=True` + `RET_C2_566_ALLOW_STUB_KB`): returns a
    client over caller-supplied in-memory lists (opt-in only).

Keys flow only through the injected secret provider; never `os.environ`, never
stored in State. Timeout/HTTP errors surface as `KBRequestError` (fail-fast).

The answer-synthesis LLM previously had its own bespoke HTTP client here
(HttpAnswerLLMClient / build_answer_llm / ANSWER_LLM_*); that has been
replaced by the framework's shared.services.llm.azure_openai_client.AzureOpenAIClient,
wired directly in the main node's _resolve_llm (AZURE_OPENAI_* secrets) — see
MultiHopRetrieveRelationshipResolveNode.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_TIMEOUT_S = 10.0
_KEY_SECRET = "PRODUCT_KG_API_KEY"
_BASE_URL_SECRET = "PRODUCT_KG_BASE_URL"
_STUB_ENV_FLAG = "RET_C2_566_ALLOW_STUB_KB"


class KBConfigError(RuntimeError):
    """Raised when a live KB client is required but its configuration is missing."""


class KBRequestError(RuntimeError):
    """Raised when a live KB fetch fails (network/HTTP/decode error)."""


class HttpProductKGClient:
    """Authenticated HTTP client for the product knowledge-graph KB."""

    def __init__(self, base_url: str, api_key: str, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s

    def _get(self, path: str) -> list[dict[str, Any]]:
        req = urllib.request.Request(
            f"{self._base_url}/{path}",
            method="GET",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            raise KBRequestError(f"product-KG fetch failed: {exc}") from exc
        records = payload.get("records", payload) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise KBRequestError("product-KG returned a non-list payload")
        return records

    def relationship_records(self) -> list[dict[str, Any]]:
        return self._get("relationships")

    def sku_records(self) -> list[dict[str, Any]]:
        return self._get("skus")


class _StubKGClient:
    """Opt-in stub over caller-supplied in-memory lists (tests / local demos)."""

    def __init__(
        self, relationship_kb: list[dict[str, Any]] | None = None, sku_kb: list[dict[str, Any]] | None = None
    ) -> None:
        self._rel = relationship_kb or []
        self._sku = sku_kb or []

    def relationship_records(self) -> list[dict[str, Any]]:
        return list(self._rel)

    def sku_records(self) -> list[dict[str, Any]]:
        return list(self._sku)


def build_kb_client(
    secrets: Any,
    *,
    allow_stub: bool = False,
    stub_relationship_kb: list[dict[str, Any]] | None = None,
    stub_sku_kb: list[dict[str, Any]] | None = None,
) -> "HttpProductKGClient | _StubKGClient":
    """Build the live product-KG KB client from the injected secret provider."""
    if allow_stub and os.environ.get(_STUB_ENV_FLAG, "").lower() in ("1", "true", "yes"):
        return _StubKGClient(relationship_kb=stub_relationship_kb, sku_kb=stub_sku_kb)

    if secrets is None:
        raise KBConfigError("no secret provider bound; cannot build a production product-KG client")

    try:
        api_key = secrets.require(_KEY_SECRET)
    except Exception as exc:  # noqa: BLE001
        raise KBConfigError(
            f"live product-KG requires the {_KEY_SECRET} secret (declared in agent.yaml "
            f"requires.secrets); set it, or opt into the demo fallback explicitly"
        ) from exc
    if not api_key:
        raise KBConfigError(f"{_KEY_SECRET} is empty; a live product-KG key is required in production")

    base_url = ""
    try:
        base_url = secrets.require(_BASE_URL_SECRET)
    except Exception:  # noqa: BLE001
        base_url = os.environ.get("PRODUCT_KG_BASE_URL", "")
    if not base_url:
        raise KBConfigError("PRODUCT_KG_BASE_URL is not configured; the live product-KG endpoint URL is required")

    return HttpProductKGClient(base_url=base_url, api_key=api_key)
