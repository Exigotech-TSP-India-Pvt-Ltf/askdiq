import pytest

from app.retrieval.retriever import decompose_query


def test_decompose_query_single_question_stays_single():
    result = decompose_query("What is Deploy IQ?")
    assert result == ["What is Deploy IQ?"]


def test_decompose_query_splits_two_question_marks():
    result = decompose_query(
        "What is the Essential Eight? How does Deploy IQ implement it?"
    )
    assert len(result) == 2
    assert result[0].endswith("?")
    assert result[1].endswith("?")
    assert "Essential Eight" in result[0]
    assert "implement" in result[1]


def test_decompose_query_splits_on_and_with_single_question_mark():
    result = decompose_query(
        "What is the Essential Eight and how does Zero Trust fit into Deploy IQ's platform?"
    )
    assert len(result) == 2
    assert "Essential Eight" in result[0]
    assert "Zero Trust" in result[1]
    assert all(part.strip().endswith("?") for part in result)


def test_decompose_query_does_not_split_short_and_clause():
    # "and" present but one side too short to be an independent topic.
    result = decompose_query("What is Deploy IQ and E8?")
    assert result == ["What is Deploy IQ and E8?"]


def test_decompose_query_does_not_split_without_and_or_second_question():
    result = decompose_query("How does Deploy IQ handle customer data?")
    assert result == ["How does Deploy IQ handle customer data?"]


def test_decompose_query_empty_string():
    assert decompose_query("") == [""]


def test_decompose_query_whitespace_only():
    assert decompose_query("   ") == [""]


@pytest.mark.parametrize(
    "query",
    [
        "What is Zero Trust?",
        "Tell me about Essential Eight and Zero Trust together and explain governance",
    ],
)
def test_decompose_query_never_returns_empty_list(query):
    result = decompose_query(query)
    assert len(result) >= 1
