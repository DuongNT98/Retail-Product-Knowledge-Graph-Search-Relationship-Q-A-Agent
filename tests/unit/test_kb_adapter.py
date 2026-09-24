# RET-C2-566 - POST-02 contract tests for the product-KG KB adapter +
# Security-MEDIUM allow-list projection before the LLM.

import pytest

from src.services.kb_adapter import (
    HttpProductKGClient,
    KBConfigError,
    build_kb_client,
)
from src.services.service import _project_for_llm, format_answer

_STUB_FLAG = "RET_C2_566_ALLOW_STUB_KB"


class _Secrets:
    def __init__(self, values):
        self._values = values

    def require(self, key):
        if key not in self._values:
            raise KeyError(key)
        return self._values[key]


# ---- KB adapter --------------------------------------------------------------

def test_live_build_ok():
    secrets = _Secrets({"PRODUCT_KG_API_KEY": "k", "PRODUCT_KG_BASE_URL": "https://kg.example"})
    assert isinstance(build_kb_client(secrets, allow_stub=False), HttpProductKGClient)


def test_missing_key_raises():
    secrets = _Secrets({"PRODUCT_KG_BASE_URL": "https://kg.example"})
    with pytest.raises(KBConfigError):
        build_kb_client(secrets, allow_stub=False)


def test_empty_key_raises():
    secrets = _Secrets({"PRODUCT_KG_API_KEY": "", "PRODUCT_KG_BASE_URL": "https://kg.example"})
    with pytest.raises(KBConfigError):
        build_kb_client(secrets, allow_stub=False)


def test_missing_url_raises(monkeypatch):
    monkeypatch.delenv("PRODUCT_KG_BASE_URL", raising=False)
    with pytest.raises(KBConfigError):
        build_kb_client(_Secrets({"PRODUCT_KG_API_KEY": "k"}), allow_stub=False)


def test_no_provider_raises():
    with pytest.raises(KBConfigError):
        build_kb_client(None, allow_stub=False)


def test_stub_requires_env_flag(monkeypatch):
    monkeypatch.delenv(_STUB_FLAG, raising=False)
    with pytest.raises(KBConfigError):
        build_kb_client(None, allow_stub=True)


def test_stub_opt_in(monkeypatch):
    monkeypatch.setenv(_STUB_FLAG, "1")
    rel = [{"source_entity_id": "sesame", "sku_id": "SKU-001"}]
    sku = [{"sku_id": "SKU-001", "supplier": "sup-a", "category": "snacks"}]
    client = build_kb_client(None, allow_stub=True, stub_relationship_kb=rel, stub_sku_kb=sku)
    assert client.relationship_records() == rel
    assert client.sku_records() == sku


# ---- Security-MEDIUM: allow-list projection before the LLM -------------------

def test_llm_projection_drops_sensitive_fields():
    resolved = [{
        "sku_id": "SKU-001", "supplier": "sup-a", "category": "snacks",
        "unit_price": "$50", "contract_term": "exclusive 3y", "margin": "20%",
    }]
    projected = _project_for_llm(resolved)
    assert projected == [{"sku_id": "SKU-001", "supplier": "sup-a", "category": "snacks"}]
    for k in ("unit_price", "contract_term", "margin"):
        assert k not in projected[0]


def test_format_answer_only_sends_allowlisted_fields_to_llm():
    captured = {}

    class SpyLLM:
        def complete(self, prompt):
            captured["prompt"] = prompt
            return {"content": "SKU-001 is compliant.", "tool_calls": [], "model": "m"}

    resolved = [{"sku_id": "SKU-001", "supplier": "sup-a", "category": "snacks",
                 "unit_price": "$50", "contract_term": "exclusive"}]
    out, degradation = format_answer(resolved, llm=SpyLLM())
    assert isinstance(out, str) and degradation is None
    # sensitive fields must not have reached the LLM prompt
    assert "unit_price" not in captured["prompt"]
    assert "contract_term" not in captured["prompt"]
    assert "$50" not in captured["prompt"]


def test_format_answer_normalises_dict_response():
    # recipe 3m: canonical BaseLLM dict must be consumed, not discarded.
    class DictLLM:
        def complete(self, prompt):
            return {"content": "Summary line.", "tool_calls": [], "model": "m"}

    out, degradation = format_answer([{"sku_id": "SKU-001", "supplier": "s", "category": "c"}], llm=DictLLM())
    assert out == "Summary line."
    assert degradation is None


def test_format_answer_reports_degradation_when_configured_llm_fails():
    """A configured LLM failing must be observable, not silently swallowed."""

    class BoomLLM:
        def complete(self, messages):
            raise ConnectionError("provider down")

    out, degradation = format_answer([{"sku_id": "SKU-001", "supplier": "s", "category": "c"}], llm=BoomLLM())
    assert degradation == "ConnectionError"
    assert "SKU-001" in out  # deterministic body still served


def test_format_answer_reports_degradation_on_non_canonical_response():
    class EmptyLLM:
        def complete(self, messages):
            return {"unexpected": True}

    out, degradation = format_answer([{"sku_id": "SKU-001", "supplier": "s", "category": "c"}], llm=EmptyLLM())
    assert degradation == "empty_or_non_canonical_response"
    assert "SKU-001" in out


def test_format_answer_without_llm_reports_no_degradation():
    out, degradation = format_answer([{"sku_id": "SKU-001", "supplier": "s", "category": "c"}])
    assert degradation is None and "SKU-001" in out
