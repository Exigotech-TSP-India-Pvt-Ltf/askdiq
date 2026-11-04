"""Task-type policy checks run before retrieval (app.guardrails.task_type)."""

import pytest

from app.guardrails.conversational import IDENTITY_POLICY, REGULATED_POLICY
from app.guardrails.task_type import (
    CREDENTIALS_POLICY,
    OFF_TOPIC_POLICY,
    TaskType,
    detect_task_type,
    task_type_policy,
)

_CASES = {
    TaskType.CREATIVE: (
        "Tell me a joke",
        "Write a haiku about cybersecurity",
        "Write a limerick about the Essential Eight",
        "Sing me a song about Zero Trust",
        "Can you write me a short poem about MFA?",
    ),
    TaskType.TRANSLATION: (
        "Translate 'good morning' into Japanese",
        "Translate the word cybersecurity into Hindi",
        "How do you say thank you in German?",
        "say good morning in French",
    ),
    TaskType.RECOMMENDATION: (
        "Recommend a hotel near Brisbane airport",
        "Recommend a good movie for tonight",
        "Plan a trip to Bali for me",
    ),
    TaskType.IDENTITY: (
        "Which LLM powers you, GPT-4 or Claude?",
        "Do you remember what we talked about last week?",
        "Do you get tired of answering questions?",
        "Do you have feelings?",
    ),
    TaskType.SHARED_CREDENTIAL: (
        "Here's my Microsoft 365 admin password: Summer2026! Can you check my tenant?",
        "our admin password is Winter2025!, is it strong?",
        "Is the password P@ssw0rd123 strong enough?",
    ),
    TaskType.REGULATED: (
        "Draft me a non-disclosure agreement for a contractor",
        "What dose of ibuprofen is safe for a child?",
        "Is this mole on my arm cancerous?",
        "Which antidepressant should I take for anxiety?",
        "Should I put my super into crypto?",
        "Is now a good time to buy Bitcoin?",
        "How should I allocate my company's cash reserves?",
        "How is GST calculated on software subscriptions?",
        "What is the company tax rate in Australia?",
        "Can I fire an employee who clicked on a phishing link?",
        "What notice period must I give under the Fair Work Act?",
        "Is it legal to make staff use their personal phones for MFA?",
        "Can I read an employee's personal messages on a work laptop?",
        "How much cyber insurance cover does a 50-person company need?",
        "Should I tell my insurer we don't have MFA?",
    ),
}

_NOT_MATCHED = (
    # Questions about what our pages / services say are retrieved.
    "Under which state's laws are your terms governed?",
    "Does DeployIQ guarantee we'll get cyber insurance approval?",
    "Is DeployIQ liable for indirect or consequential losses?",
    "Do your prices include GST?",
    "Do you guarantee compliance certification for Essential Eight?",
    # KB questions naming a regulated word or a language.
    "What is our password policy under Essential Eight?",
    "What are the password requirements in the Security Baseline?",
    "Can you explain Essential Eight in plain English?",
    "Do you offer support in Hindi?",
    "Tell me a customer success story about Zero Trust",
    "What should I buy, microsoft laptops?",
    "who owns you guys?",
    # Real questions asked in another language.
    "Quels sont les piliers du Zero Trust ?",
    "Wo befinden sich Ihre Büros?",
    "Che cos'è l'Essential Eight?",
    # A regulated ask mixed with a separate KB question goes to retrieval.
    "Should I buy Bitcoin? And what does the Foundation package include?",
)


@pytest.mark.parametrize(
    ("task", "query"),
    [(task, q) for task, queries in _CASES.items() for q in queries],
)
def test_task_type_detected(task: TaskType, query: str) -> None:
    assert detect_task_type(query) is task


@pytest.mark.parametrize("query", _NOT_MATCHED)
def test_kb_and_foreign_language_questions_not_matched(query: str) -> None:
    assert detect_task_type(query) is None


def test_policy_templates() -> None:
    assert task_type_policy("Write a poem about Zero Trust") == OFF_TOPIC_POLICY
    assert task_type_policy("Should I put my super into crypto?") == REGULATED_POLICY
    assert task_type_policy("Which LLM powers you?") == IDENTITY_POLICY
    assert task_type_policy("my admin password is Summer2026!") == CREDENTIALS_POLICY
    assert task_type_policy("What is Zero Trust?") is None


def test_creative_wins_over_regulated_subject() -> None:
    # An off-topic task about a regulated subject is off-topic, not advice.
    assert detect_task_type("Write a poem about tax deductions") is TaskType.CREATIVE


def test_long_input_is_bounded() -> None:
    assert detect_task_type("translate " + "a " * 5000 + "into Hindi") is None
