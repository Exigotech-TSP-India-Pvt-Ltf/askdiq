import uuid

from app.models.db_models import Chunk
from app.retrieval.vector_store import VectorStore


def _chunk(document_id, score) -> tuple[Chunk, float]:
    chunk = Chunk(
        id=uuid.uuid4(),
        document_id=document_id,
        content=f"content for {document_id} @ {score}",
        embedding=[0.0, 0.0],
        strategy="structure_aware",
        chunk_index=0,
        chunk_metadata={},
    )
    return chunk, score


def test_select_diverse_chunks_empty_candidates_returns_empty():
    assert VectorStore._select_diverse_chunks([], top_k=5) == []


def test_select_diverse_chunks_single_document_returns_top_k():
    doc = uuid.uuid4()
    candidates = [_chunk(doc, score) for score in [0.9, 0.8, 0.7, 0.6, 0.5]]

    selected = VectorStore._select_diverse_chunks(candidates, top_k=3)

    assert len(selected) == 3
    assert [round(score, 1) for _, score in selected] == [0.9, 0.8, 0.7]


def test_select_diverse_chunks_guarantees_second_document_representation():
    doc_a = uuid.uuid4()  # strongest document, many candidates
    doc_b = uuid.uuid4()  # second-strongest, fewer candidates

    candidates = (
        [_chunk(doc_a, score) for score in [0.95, 0.94, 0.93, 0.92, 0.91]]
        + [_chunk(doc_b, score) for score in [0.80, 0.79]]
    )
    candidates.sort(key=lambda item: item[1], reverse=True)

    selected = VectorStore._select_diverse_chunks(candidates, top_k=4)

    selected_docs = {str(chunk.document_id) for chunk, _ in selected}
    assert str(doc_a) in selected_docs
    assert str(doc_b) in selected_docs  # not starved by the stronger document


def test_select_diverse_chunks_backfills_when_quota_docs_run_out():
    doc_a = uuid.uuid4()
    doc_b = uuid.uuid4()

    candidates = [_chunk(doc_a, 0.9), _chunk(doc_b, 0.8)]

    # top_k larger than available candidates — should just return both,
    # not error or duplicate.
    selected = VectorStore._select_diverse_chunks(candidates, top_k=5)

    assert len(selected) == 2


def test_select_diverse_chunks_never_returns_duplicate_chunk_ids():
    doc = uuid.uuid4()
    candidates = [_chunk(doc, score) for score in [0.9, 0.8, 0.7]]

    selected = VectorStore._select_diverse_chunks(candidates, top_k=10)

    ids = [chunk.id for chunk, _ in selected]
    assert len(ids) == len(set(ids))
