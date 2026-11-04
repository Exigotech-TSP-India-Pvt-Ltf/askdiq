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
    context = prompt.split("Facts:\n", 1)[1].split("\n\nQuestion:", 1)[0]

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
        "[Source 1 | Document: cis-benchmark-assessment.md | Section: Automated Controls]\n"
        "DeployIQ automates CIS benchmark assessments." in context
    )
    assert (
        "[Source 2 | Document: iso-27001-technical-controls.md]\n"
        "DeployIQ aligns security controls with ISO 27001." in context
    )
    assert "Plain string chunk without metadata." in context


async def test_call_llm_caps_output_and_requests_quote_backed_json(monkeypatch):
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

    monkeypatch.setattr(prompts._answer_client.chat.completions, "create", fake_create)

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
    assert (
        captured_kwargs["max_completion_tokens"]
        == prompts.settings.llm_max_completion_tokens
    )
    assert "reasoning_effort" in captured_kwargs
    schema = captured_kwargs["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert set(schema["schema"]["required"]) == {"policy", "parts", "claims"}


async def test_streamed_answer_is_assembled_with_usage_and_ttft():
    from types import SimpleNamespace

    def chunk(text=None, finish=None, usage=None):
        choices = (
            [SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)]
            if text is not None or finish
            else []
        )
        return SimpleNamespace(choices=choices, usage=usage)

    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8)

    class FakeStream:
        closed = False

        def __aiter__(self):
            async def gen():
                for c in (
                    chunk(),  # Azure prompt-filter chunk: no choices
                    chunk('{"claims"'),
                    chunk(": []}", finish="stop"),
                    chunk(usage=usage),
                ):
                    yield c

            return gen()

        async def close(self):
            FakeStream.closed = True

    response = await prompts._collect_stream(FakeStream(), 0.0)

    assert response.choices[0].message.content == '{"claims": []}'
    assert response.choices[0].finish_reason == "stop"
    assert response.usage is usage
    assert response.ttft_ms is not None
    assert FakeStream.closed


def test_answer_client_does_not_retry_under_the_orchestrator():
    # One retry layer only: the orchestrator retries transient errors once.
    assert prompts._answer_client.max_retries == 0


def test_system_prompt_states_rules_once_and_styles_only_change_format():
    from app.harness import prompt_catalog

    rules = prompt_catalog.common_rules()
    for style, shape in (("current", "general"), ("adaptive", "quick_fact")):
        system = prompts.get_system_prompt(style, shape)
        assert system.count(rules) == 1
        # No style block restates a refusal rule or the no-answer line.
        assert prompt_catalog.NO_ANSWER_LINE not in system
    # Each rule once, telegraphic: grounding + compliance policy (rules 5-7,
    # policy field) + the owner's length rules. Was 857 words before the trim.
    assert len(prompts.get_system_prompt("current", "general").split()) <= 620


def test_answer_schema_uses_compact_keys_quote_before_text():
    from app.harness import prompt_catalog

    schema = prompt_catalog.answer_format()["json_schema"]["schema"]
    claim = schema["properties"]["claims"]["items"]
    part = schema["properties"]["parts"]["items"]
    assert claim["required"] == ["p", "s", "q", "t", "k"]
    assert list(claim["properties"]) == ["p", "s", "q", "t", "k"]
    assert claim["properties"]["k"]["enum"] == ["s", "i"]
    assert part["required"] == ["a", "c"]
    # Every compact key is documented in the prompt.
    system = prompts.get_system_prompt("current", "general")
    for key in ("a:", "c:", "p:", "s:", "q =", "t:", "k:"):
        assert key in system


def test_system_prompt_states_the_length_rules():
    system = prompts.get_system_prompt("current", "general")
    for needle in (
        "1-2 sentences",
        "2-4 sentences",
        "up to 6",
        "120 words",
        "at most 12 words",
    ):
        assert needle in system
