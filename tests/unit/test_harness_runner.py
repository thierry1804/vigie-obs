import pytest

from agent.harness import runner


class FakeResultMessage:
    def __init__(self, result, usage):
        self.result = result
        self.usage = usage


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
