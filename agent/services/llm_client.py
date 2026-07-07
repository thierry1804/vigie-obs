"""Client LLM avec support mock pour tests CI."""

import os

from anthropic import Anthropic

from agent.config import MODEL_TRIAGE

_client: Anthropic | None = None


def _mock_enabled() -> bool:
    return os.environ.get("VIGIE_MOCK_LLM", "").lower() in ("1", "true", "yes")


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def mock_response(model: str, user_content: str, tools: list | None = None):
    """Réponse fixture quand VIGIE_MOCK_LLM=1."""

    class Block:
        def __init__(self, block_type, **kwargs):
            self.type = block_type
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Response:
        stop_reason = "end_turn"
        usage = type("U", (), {"input_tokens": 100, "output_tokens": 50})()
        content = [
            Block(
                "text",
                text=(
                    "Réponse mock VIGIE. "
                    "FAITS : données simulées. HYPOTHÈSES : aucune conclusion réelle sans API."
                ),
            )
        ]

    text = str(user_content).lower()
    if model == MODEL_TRIAGE:
        is_noise = "heartbeat" in text or "healthcheck" in text
        Response.content = [
            Block(
                "text",
                text='{"is_anomaly": false, "reason": "bruit connu"}'
                if is_noise
                else '{"is_anomaly": true, "reason": "anomalie plausible"}',
            )
        ]
    elif "taxonom" in text or "événements métier" in text:
        Response.content = [
            Block(
                "text",
                text=(
                    "events:\n"
                    "  - name: order_created\n"
                    "    patterns: ['commande créée', 'order created']\n"
                    "  - name: payment_received\n"
                    "    patterns: ['paiement reçu', 'payment received']\n"
                ),
            )
        ]
    elif tools and ("erreur" in text or "error" in text):
        Response.stop_reason = "tool_use"
        Response.content = [
            Block("tool_use", id="mock_tool_1", name="query_loki", input={"logql": '{level="error"}'}),
        ]
    return Response()


def create_message(model: str, max_tokens: int, system: str, messages: list, tools: list | None = None):
    if _mock_enabled():
        user_msg = messages[-1]["content"] if messages else ""
        if isinstance(user_msg, list):
            user_msg = str(user_msg)
        return mock_response(model, user_msg, tools)
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    return get_client().messages.create(**kwargs)
