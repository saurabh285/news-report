import os
import pytest

from ai import llm_client


def test_resolve_provider_precedence(tmp_path, monkeypatch):
    # explicit env var wins even if config and keys exist
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    res = llm_client.resolve_provider(configured="anthropic")
    assert res == "openai"

    # config value used when no env override
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    res = llm_client.resolve_provider(configured="gemini")
    assert res == "gemini"

    # auto-detect by checking key order
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    res = llm_client.resolve_provider(configured="")
    assert res == "anthropic"

    # missing everything should raise
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm_client.resolve_provider(configured="")


def test_resolve_model_checks_and_defaults(monkeypatch):
    # known providers return defaults
    assert llm_client.resolve_model("gemini") == "gemini-1.5-flash"
    assert llm_client.resolve_model("anthropic") == "claude-haiku-4-5-20251001"

    # per-provider env var override
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    assert llm_client.resolve_model("openai") == "gpt-test"
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    # config value used when present
    assert llm_client.resolve_model("openai", configured="gpt-model") == "gpt-model"

    # invalid provider name raises
    with pytest.raises(RuntimeError):
        llm_client.resolve_model("foobar")


def test_call_llm_invalid_provider():
    with pytest.raises(RuntimeError):
        llm_client.call_llm("hi", "sys", "nosuch", "m")


def test_call_llm_missing_keys(monkeypatch):
    # using gemini without key should raise RuntimeError
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm_client.call_llm("msg", "sys", "gemini", "m")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm_client.call_llm("msg", "sys", "anthropic", "m")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm_client.call_llm("msg", "sys", "openai", "m")
