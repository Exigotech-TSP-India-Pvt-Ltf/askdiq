import tiktoken

from app.harness import prompts
from app.schemas.query import ChatTurn


_ENCODING = tiktoken.get_encoding("o200k_base")


def test_context_is_capped_and_keeps_highest_ranked_chunks_first(monkeypatch):
    monkeypatch.setattr(prompts.settings, "llm_context_max_tokens", 12)
    chunks = [
        "highest ranked evidence " * 20,
        "lower ranked evidence that should be dropped",
    ]

    context = prompts._format_context(chunks)

    assert len(_ENCODING.encode(context)) <= 12
    assert context.startswith("highest ranked evidence")
    assert "lower ranked evidence" not in context


def test_history_budget_keeps_recent_turns_and_caps_tokens(monkeypatch):
    monkeypatch.setattr(prompts.settings, "llm_history_max_tokens", 30)
    history = [
        ChatTurn(role="user", content="old question " * 50),
        ChatTurn(role="assistant", content="old answer " * 50),
        ChatTurn(role="user", content="latest follow-up"),
        ChatTurn(role="assistant", content="latest answer"),
    ]

    formatted = prompts._format_history("what about it?", history)

    assert len(_ENCODING.encode(formatted)) <= 30
    assert "latest follow-up" in formatted
    assert "latest answer" in formatted
    assert "old question" not in formatted


def test_standalone_question_does_not_send_history():
    history = [ChatTurn(role="user", content="Earlier topic")]

    assert prompts._format_history("What is Zero Trust?", history) == ""


def test_prompt_omits_examples_and_enforces_context_budget(monkeypatch):
    monkeypatch.setattr(prompts.settings, "llm_context_max_tokens", 24)
    prompt = prompts.build_prompt(
        "What does DeployIQ do?",
        ["DeployIQ provides governed security implementation. " * 20],
    )
    context = prompt.split("Context:\n", 1)[1].split("\n\nQuestion:", 1)[0]

    assert "Example 1" not in prompt
    assert "Example 2" not in prompt
    assert len(_ENCODING.encode(context)) <= 24
