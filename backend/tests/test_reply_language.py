"""Reply-language requests, native-script catalogue names, Indic terminators."""

# pylint: disable=protected-access

from __future__ import annotations

import pytest

from app.guardrails.task_type import (
    is_reply_language,
    reply_language,
    strip_reply_language,
    task_type_policy,
)
from app.harness import claims
from app.harness.orchestrator import _foreign_question, _language_note
from app.harness.prompt_catalog import LANGUAGE_NOTE
from app.retrieval.retriever import expand_retrieval_query


@pytest.mark.parametrize(
    ("query", "language", "stripped"),
    [
        ("What is Zero Trust in Gujarati?", "Gujarati", "What is Zero Trust"),
        ("What is Zero Trust in Russian?", "Russian", "What is Zero Trust"),
        ("what is deployiq, explain in german", "German", "what is deployiq"),
        ("what is deployiq, answer in bengali", "Bengali", "what is deployiq"),
        (
            "yeh deployiq kya hai , russian mein jawaab do",
            "Russian",
            "yeh deployiq kya hai",
        ),
        ("deployiq kya hai? hindi me batao", "Hindi", "deployiq kya hai"),
        ("zero trust kya hai marathi mein samjhao", "Marathi", "zero trust kya hai"),
    ],
)
def test_reply_language_request(query: str, language: str, stripped: str) -> None:
    assert reply_language(query) == language
    assert strip_reply_language(query) == stripped
    note = _language_note(query)
    assert language in note
    assert note != LANGUAGE_NOTE


@pytest.mark.parametrize(
    "query",
    ["What is Zero Trust?", "deployiq kya hai?", "explain in plain english"],
)
def test_no_reply_language(query: str) -> None:
    assert reply_language(query) is None
    assert _language_note(query) == ""


def test_whole_message_reply_language_follow_up() -> None:
    assert is_reply_language("russian mein jawaab do")
    assert reply_language("explain in tamil") == "Tamil"


def test_native_question_keeps_generic_note() -> None:
    assert _language_note("Что такое Zero Trust?") == LANGUAGE_NOTE
    assert _foreign_question("Zero Trust क्या है?")
    assert not _foreign_question("What is Zero Trust in Gujarati?")


@pytest.mark.parametrize(
    "query", ["what is hello in german?", "translate this into german: we sell shoes"]
)
def test_translation_tasks_still_declined(query: str) -> None:
    assert task_type_policy(query) is not None


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("झिरो ट्रस्ट म्हणजे काय?", "Zero Trust"),
        ("ज़ीरो ट्रस्ट क्या है?", "Zero Trust"),
        ("ઝીરો ટ્રસ્ટ શું છે?", "Zero Trust"),
        ("एसेंशियल एट क्या है?", "Essential Eight"),
        ("એસેન્શિયલ એઈટ શું છે?", "Essential Eight"),
        ("डिप्लॉय आईक्यू क्या है?", "Deploy IQ"),
        ("ડિપ્લોય આઈક્યુ શું છે?", "Deploy IQ"),
        ("माइक्रोसॉफ्ट 365 सुरक्षा", "Microsoft 365"),
        ("आईएसओ 27001 क्या है?", "ISO 27001"),
        ("આઈએસઓ 42001 શું છે?", "ISO 42001"),
        ("सीआईएस क्या है?", "CIS"),
    ],
)
def test_native_script_names_expanded(query: str, name: str) -> None:
    assert name in expand_retrieval_query(query)


def test_native_expansion_leaves_other_text() -> None:
    query = "मुझे जूते चाहिए"
    assert expand_retrieval_query(query) == query


def test_danda_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proposed claims.py fix (agent_lang_claims.diff): a danda ends a
    sentence, so no "." is appended after it."""
    sentence = "Zero Trust एक सुरक्षा मॉडल है।"
    monkeypatch.setattr(claims, "_TERMINAL", claims._TERMINAL + "।॥")
    assert claims._sentence(sentence) == sentence
    assert claims._sentence("Zero Trust") == "Zero Trust."


@pytest.mark.parametrize(
    "query",
    [
        "Was kostet Zero Trust?",
        "¿Cuánto cuesta Zero Trust?",
        "Quel est le prix de Zero Trust?",
        "Zero Trust kitna price hai?",
        "Сколько стоит Zero Trust?",
        "Zero Trust की कीमत क्या है?",
    ],
)
def test_price_asks_in_other_languages_are_buying(query: str) -> None:
    from app.harness.clarify import is_foreign_price_ask  # noqa: PLC0415

    assert is_foreign_price_ask(query)


@pytest.mark.parametrize("query", ["Was ist Zero Trust?", "Costa Rica office?"])
def test_non_price_asks_are_not_buying(query: str) -> None:
    from app.harness.clarify import is_foreign_price_ask  # noqa: PLC0415

    assert not is_foreign_price_ask(query)


def test_danda_ends_a_sentence() -> None:
    from app.harness import claims  # noqa: PLC0415

    assert "।" in claims._TERMINAL  # noqa: SLF001  # pylint: disable=protected-access
