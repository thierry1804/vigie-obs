import pytest

from agent.db.models import Tenant
from agent.db.session import get_session
from agent.harness import runner


class FakeResultMessage:
    def __init__(self, result, usage, is_error=False, errors=None):
        self.result = result
        self.usage = usage
        self.is_error = is_error
        self.errors = errors


@pytest.mark.asyncio
async def test_run_agent_mock_short_circuits(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "1")
    answer = await runner.run_agent("diagnostic", "question", tenant_id="acme", endpoint="ask")
    assert "Réponse mock VIGIE" in answer


@pytest.mark.asyncio
async def test_run_agent_real_path_extracts_final_text_and_records_usage(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")

    async def fake_query(*, prompt, options=None, transport=None):
        yield FakeResultMessage(
            result="Diagnostic : latence Prometheus due à un pic de charge.",
            usage={"input_tokens": 120, "output_tokens": 45},
        )

    recorded = []

    def fake_record_usage(tenant_id, endpoint, model, input_tokens, output_tokens):
        recorded.append((tenant_id, endpoint, model, input_tokens, output_tokens))

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(runner, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(runner, "record_usage", fake_record_usage)

    answer = await runner.run_agent(
        "diagnostic", "pourquoi ça plante ?", tenant_id="acme", endpoint="ask"
    )

    assert answer == "Diagnostic : latence Prometheus due à un pic de charge."
    assert recorded == [("acme", "ask", "claude-sonnet-4-6", 120, 45)]


@pytest.mark.asyncio
async def test_run_agent_real_path_no_result_message(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")

    async def fake_query(*, prompt, options=None, transport=None):
        return
        yield  # pragma: no cover - générateur vide

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(runner, "ResultMessage", FakeResultMessage)

    answer = await runner.run_agent("diagnostic", "question", tenant_id="acme", endpoint="ask")
    assert "Erreur" in answer


@pytest.mark.asyncio
async def test_run_agent_real_path_blocked_when_budget_exhausted(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")

    with get_session() as session:
        session.add(
            Tenant(id="acme", name="Acme", budget_llm_tokens=1000, tokens_used=1000)
        )
        session.commit()

    async def fake_query(*, prompt, options=None, transport=None):
        raise AssertionError("query() must not be called when budget is exhausted")
        yield  # pragma: no cover - générateur factice, jamais atteint

    def fake_build_diagnostic_options(tenant_id, system_prompt=None):
        raise AssertionError(
            "build_diagnostic_options() must not be called when budget is exhausted"
        )

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(runner, "ResultMessage", FakeResultMessage)
    monkeypatch.setitem(
        runner._PRESET_BUILDERS, "diagnostic", fake_build_diagnostic_options
    )

    answer = await runner.run_agent("diagnostic", "question", tenant_id="acme", endpoint="ask")

    assert "Budget" in answer


@pytest.mark.asyncio
async def test_run_agent_real_path_returns_error_on_exception(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")

    async def fake_query(*, prompt, options=None, transport=None):
        raise RuntimeError("CLI introuvable")
        yield  # pragma: no cover - générateur factice, jamais atteint

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(runner, "ResultMessage", FakeResultMessage)

    answer = await runner.run_agent("diagnostic", "question", tenant_id="acme", endpoint="ask")

    assert "Erreur" in answer
    assert "CLI introuvable" in answer


@pytest.mark.asyncio
async def test_run_agent_real_path_returns_error_when_result_message_is_error(monkeypatch):
    monkeypatch.setenv("VIGIE_MOCK_LLM", "0")

    async def fake_query(*, prompt, options=None, transport=None):
        yield FakeResultMessage(
            result="",
            usage={"input_tokens": 10, "output_tokens": 0},
            is_error=True,
            errors=["overloaded_error"],
        )

    recorded = []

    def fake_record_usage(tenant_id, endpoint, model, input_tokens, output_tokens):
        recorded.append((tenant_id, endpoint, model, input_tokens, output_tokens))

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(runner, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(runner, "record_usage", fake_record_usage)

    answer = await runner.run_agent("diagnostic", "question", tenant_id="acme", endpoint="ask")

    assert "Erreur" in answer
    assert "overloaded_error" in answer
    # L'usage doit tout de même être enregistré : le CLI a consommé des tokens.
    assert recorded == [("acme", "ask", "claude-sonnet-4-6", 10, 0)]
