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


def test_format_context_with_source_chunk_metadata():
    from app.schemas.query import SourceChunk

    chunk1 = SourceChunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        content="DeployIQ automates CIS benchmark assessments.",
        score=0.95,
        metadata={
            "source_name": "cis-benchmark-assessment.md",
            "section_heading": "Automated Controls",
        },
    )
    chunk2 = SourceChunk(
        chunk_id="chunk-2",
        document_id="doc-2",
        content="DeployIQ aligns security controls with ISO 27001.",
        score=0.88,
        metadata={
            "source_name": "iso-27001-technical-controls.md",
        },
    )
    chunk3 = "Plain string chunk without metadata."

    context = prompts._format_context([chunk1, chunk2, chunk3])

    assert (
        "[Document: cis-benchmark-assessment.md | Section: Automated Controls]\n"
        "DeployIQ automates CIS benchmark assessments." in context
    )
    assert (
        "[Document: iso-27001-technical-controls.md]\n"
        "DeployIQ aligns security controls with ISO 27001." in context
    )
    assert "Plain string chunk without metadata." in context


async def test_call_llm_omits_temperature_and_max_completion_tokens(monkeypatch):
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content="Mocked answer from DeployIQ"))
    ]
    mock_response.usage = MagicMock(
        prompt_tokens=25,
        completion_tokens=10,
        total_tokens=35,
    )

    captured_kwargs = {}

    async def fake_create(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_response

    monkeypatch.setattr(prompts._client.chat.completions, "create", fake_create)

    answer, usage = await prompts.call_llm("What is DeployIQ?", ["Some context"])

    assert answer == "Mocked answer from DeployIQ"
    assert usage == {
        "prompt_tokens": 25,
        "completion_tokens": 10,
        "total_tokens": 35,
        "reasoning_tokens": 0,
        "visible_tokens": 10,
        "cached_tokens": 0,
    }
    assert "temperature" not in captured_kwargs
    assert "max_completion_tokens" not in captured_kwargs
    assert "reasoning_effort" in captured_kwargs
