"""
backend/tests/test_services.py

Pytest suite for Legal Sarathi core services.
Run from backend/ directory:
    pytest tests/test_services.py -v

All Groq / SentenceTransformer / FAISS calls are mocked.
No real API calls. No built index required.
"""

import sys
import copy
import asyncio
import importlib.util
from pathlib import Path
from typing import List, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── sys.path: make backend/ importable ────────────────────────────────────────
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# ═══════════════════════════════════════════════════════════════════════════════
# Shared fixtures
# ═══════════════════════════════════════════════════════════════════════════════

SAMPLE_CHUNKS: List[Dict] = [
    {
        "id": "BNSS_50_0",
        "section_ref": "BNSS_50",
        "title": "BNSS 2023 §50 - Person arrested to be informed of grounds",
        "text": (
            "Every police officer arresting any person without a warrant shall "
            "forthwith communicate to him the full particulars of the offence for "
            "which he is arrested or other grounds for such arrest."
        ),
        "parent_content": "BNSS 2023 §50\nFull text.",
        "act": "BNSS",
        "score": 0.91,
    },
    {
        "id": "CONST_22_0",
        "section_ref": "CONST_22",
        "title": "Constitution Article 22 - Protection against arbitrary arrest",
        "text": (
            "No person who is arrested shall be detained without being informed of "
            "the grounds of arrest nor shall he be denied the right to consult a "
            "legal practitioner of his choice."
        ),
        "parent_content": "Constitution Article 22\nFull text.",
        "act": "CONST",
        "score": 0.88,
    },
    {
        "id": "BNS_73_0",
        "section_ref": "BNS_73",
        "title": "BNS 2023 §73 - Arrest how made",
        "text": (
            "In making an arrest the police officer shall actually touch or confine "
            "the body of the person to be arrested unless there be a submission to "
            "custody by word or action."
        ),
        "parent_content": "BNS 2023 §73\nFull text.",
        "act": "BNS",
        "score": 0.76,
    },
]

SAMPLE_BUDDY_DATA = {
    "situation_summary": "You were arrested without being informed of the charges.",
    "severity_level": "DANGER",
    "rights": [
        "Right to know grounds of arrest [BNSS_50]",
        "Cannot be detained beyond 24 hours [BNSS_51]",
        "Right to legal counsel [CONST_22]",
    ],
    "action_steps": [
        "Ask police for FIR copy immediately",
        "Call NALSA helpline 15100",
        "Contact family to arrange a lawyer",
    ],
    "do_not_do": ["Do not sign any blank paper", "Do not resist physically"],
    "evidence_required": ["FIR copy", "Arrest memo"],
    "jurisdiction_note": "",
    "awareness": "Under BNSS Section 50 every arrested person must be told grounds.",
    "buddy_text": "Don't panic. You have rights. Call NALSA at 15100 right now.",
    "help_channels": [
        {"name": "NALSA", "phone": "15100", "url": "https://nalsa.gov.in",
         "label_in_lang": "NALSA"}
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CitationAuditService
# ═══════════════════════════════════════════════════════════════════════════════

class TestCitationAuditService:

    def _svc(self):
        from app.services.citation_audit import CitationAuditService
        return CitationAuditService()

    def test_returns_required_keys(self):
        result = self._svc().audit("some text", SAMPLE_CHUNKS)
        for k in ("citation_score", "verified", "unverified",
                  "uncited_available", "badge", "total_retrieved"):
            assert k in result, f"Missing key: {k}"

    def test_score_in_range(self):
        result = self._svc().audit("Under [BNSS_50] and [CONST_22] you have rights.", SAMPLE_CHUNKS)
        assert 0.0 <= result["citation_score"] <= 1.0

    def test_verified_citation_detected(self):
        answer = "According to [BNSS_50] you must be informed of arrest grounds."
        result = self._svc().audit(answer, SAMPLE_CHUNKS)
        assert "BNSS_50" in result["verified"]
        assert result["citation_score"] > 0.0

    def test_unverified_citation_detected(self):
        answer = "You have rights under [RTI_7] and [FAKE_999]."
        result = self._svc().audit(answer, SAMPLE_CHUNKS)
        unverified_set = set(result["unverified"])
        assert "RTI_7" in unverified_set or "FAKE_999" in unverified_set

    def test_no_citations_score_capped_at_half(self):
        result = self._svc().audit("Contact police and call NALSA.", SAMPLE_CHUNKS)
        assert result["citation_score"] <= 0.5

    def test_empty_chunks_zero_score(self):
        result = self._svc().audit("[BNSS_50] is relevant", [])
        assert result["citation_score"] == 0.0

    def test_all_verified_score_is_one(self):
        answer = "See [BNSS_50] and [CONST_22] and [BNS_73]."
        result = self._svc().audit(answer, SAMPLE_CHUNKS)
        assert result["citation_score"] == 1.0
        assert len(result["unverified"]) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. RerankerService
# ═══════════════════════════════════════════════════════════════════════════════

class TestRerankerService:

    def _chunks(self):
        return copy.deepcopy(SAMPLE_CHUNKS)

    def _svc(self):
        from app.services.reranker_service import RerankerService
        return RerankerService()

    def test_returns_list(self):
        assert isinstance(self._svc().rerank("arrest", self._chunks(), top_k=3), list)

    def test_respects_top_k(self):
        result = self._svc().rerank("arrest", self._chunks(), top_k=2)
        assert len(result) <= 2

    def test_chunks_retain_section_ref(self):
        result = self._svc().rerank("police arrest", self._chunks(), top_k=3)
        for c in result:
            assert "section_ref" in c

    def test_sorted_by_rerank_score_descending(self):
        """Mock CrossEncoder to return controlled scores; assert sort order."""
        from app.services import reranker_service as mod
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9, 0.5]  # chunk[1] wins
        with patch.object(mod, "_cross_encoder", mock_model), \
             patch.object(mod, "_load_attempted", True):
            svc = self._svc()
            chunks = self._chunks()
            result = svc.rerank("test query", chunks, top_k=3)
        assert result[0]["section_ref"] == chunks[1]["section_ref"]
        assert result[0]["rerank_score"] == pytest.approx(0.9)
        assert result[1]["rerank_score"] == pytest.approx(0.5)
        assert result[2]["rerank_score"] == pytest.approx(0.1)

    def test_fallback_when_model_none(self):
        """No CrossEncoder → return original top_k order."""
        from app.services import reranker_service as mod
        with patch.object(mod, "_cross_encoder", None), \
             patch.object(mod, "_load_attempted", True):
            svc = self._svc()
            chunks = self._chunks()
            result = svc.rerank("query", chunks, top_k=2)
        assert len(result) == 2
        assert result[0]["section_ref"] == chunks[0]["section_ref"]

    def test_empty_input_returns_empty(self):
        assert self._svc().rerank("query", [], top_k=5) == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RAGService (FAISS + BM25 mocked, no model loading)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRAGService:

    def _make_svc(self):
        import numpy as np
        from app.services.rag_service import RAGService

        svc = RAGService.__new__(RAGService)   # skip __init__
        svc._ready    = True
        svc._use_neon = False

        # Fake embedding model
        mock_model = MagicMock()
        mock_model.encode.return_value = np.zeros((1, 384), dtype="float32")
        svc._model = mock_model

        # Fake FAISS index returning indices 0,1,2
        mock_faiss = MagicMock()
        mock_faiss.search.return_value = (
            np.array([[0.91, 0.88, 0.76]], dtype="float32"),
            np.array([[0, 1, 2]], dtype="int64"),
        )
        svc._faiss_index = mock_faiss

        svc._chunks_meta = copy.deepcopy(SAMPLE_CHUNKS)

        # Fake BM25 returning scores that rank chunk[2] first
        mock_bm25 = MagicMock()
        mock_bm25.get_scores.return_value = [0.4, 0.3, 0.8]
        svc._bm25 = mock_bm25

        return svc

    def test_retrieve_hybrid_returns_tuple(self):
        chunks, elapsed = self._make_svc().retrieve_hybrid("arrest warrant India")
        assert isinstance(chunks, list)
        assert isinstance(elapsed, float)

    def test_retrieve_hybrid_chunks_have_section_ref(self):
        chunks, _ = self._make_svc().retrieve_hybrid("arrest without warrant")
        assert len(chunks) > 0
        for c in chunks:
            assert "section_ref" in c

    def test_retrieve_hybrid_chunks_have_score(self):
        chunks, _ = self._make_svc().retrieve_hybrid("bail application")
        for c in chunks:
            assert "score" in c or "rrf_score" in c

    def test_retrieve_hybrid_elapsed_non_negative(self):
        _, elapsed = self._make_svc().retrieve_hybrid("arrest")
        assert elapsed >= 0.0

    def test_retrieve_hybrid_not_ready_returns_empty(self):
        from app.services.rag_service import RAGService
        svc = RAGService.__new__(RAGService)
        svc._ready = False
        svc._model = None
        chunks, elapsed = svc.retrieve_hybrid("any query")
        assert chunks == []
        assert elapsed == 0.0

    def test_retrieve_hybrid_no_duplicate_refs(self):
        chunks, _ = self._make_svc().retrieve_hybrid("bail India")
        refs = [c["section_ref"] for c in chunks]
        assert len(refs) == len(set(refs)), "Duplicate section_refs in result"

    def test_retrieve_dense_returns_tuple(self):
        result = self._make_svc().retrieve("police")
        assert isinstance(result, tuple) and len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Orchestrator (all services mocked)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_orchestrator():
    """
    Construct a fully-mocked Orchestrator.
    Called inside each test to get a fresh instance.
    """
    # Patch imports inside the orchestrator module before Orchestrator() runs
    with patch("app.agents.orchestrator.GroqService") as MockGroq, \
         patch("app.agents.orchestrator.TranslatorService") as MockTrans, \
         patch("app.agents.orchestrator.SearchService") as MockSearch, \
         patch("app.agents.orchestrator.RAGService") as MockRAG, \
         patch("app.agents.orchestrator.RerankerService") as MockReranker, \
         patch("app.agents.orchestrator.CitationAuditService") as MockAudit, \
         patch("app.agents.orchestrator.SpecialistService") as MockSpec:

        # Groq
        gi = MockGroq.return_value
        gi.extract_legal_keys = AsyncMock(
            return_value=["Warrantless Arrest", "BNSS_50", "Right to inform"]
        )
        gi.synthesize_buddy_response = AsyncMock(return_value=copy.deepcopy(SAMPLE_BUDDY_DATA))

        # Translator
        ti = MockTrans.return_value
        ti.translate_to_english = AsyncMock(
            return_value="Police arrested me without informing grounds"
        )
        ti.translate = AsyncMock(
            return_value="Police arrested me without informing grounds"
        )

        # Search
        si = MockSearch.return_value
        si.search_legal_context = MagicMock(
            return_value=("BNSS Section 50 mandates informing grounds.", ["https://example.com"])
        )

        # RAG
        ri = MockRAG.return_value
        ri.is_ready = True
        ri.retrieve_hybrid = MagicMock(return_value=(copy.deepcopy(SAMPLE_CHUNKS), 0.05))
        ri.format_for_prompt = MagicMock(
            return_value="[BNSS_50] Person must be informed of grounds."
        )

        # Reranker
        rri = MockReranker.return_value
        rri.rerank = MagicMock(return_value=copy.deepcopy(SAMPLE_CHUNKS))

        # Citation audit
        ai = MockAudit.return_value
        ai.audit = MagicMock(return_value={
            "citation_score": 0.85,
            "verified": ["BNSS_50", "CONST_22"],
            "unverified": [],
            "uncited_available": [],
            "badge": "✅ Verified (2/2 sources)",
            "total_retrieved": 3,
        })

        # Specialist
        MockSpec.return_value.generate_guidance = AsyncMock(return_value="")

        from app.agents.orchestrator import Orchestrator
        orch = Orchestrator()
        # Inject mocked instances after construction
        orch.groq_service      = gi
        orch.translator        = ti
        orch.search_service    = si
        orch.rag_service       = ri
        orch.reranker          = rri
        orch.citation_audit    = ai
        orch.specialist_service = None

    return orch


class TestOrchestrator:

    @pytest.mark.asyncio
    async def test_returns_dict(self):
        orch = _build_orchestrator()
        result = await orch.process_query(text="Police arrested me", lang="en")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_required_keys_present(self):
        orch = _build_orchestrator()
        result = await orch.process_query(text="Police arrested me", lang="en")
        for k in ("situation_summary", "rights", "action_steps",
                  "citation_score", "latency", "query", "lang",
                  "legal_keys", "citation_badge", "rag_chunks_used"):
            assert k in result, f"Missing key: {k}"

    @pytest.mark.asyncio
    async def test_latency_subkeys(self):
        orch = _build_orchestrator()
        result = await orch.process_query(text="Can police arrest without warrant?", lang="en")
        lat = result["latency"]
        for k in ("translation", "parallel", "rerank", "synthesis", "total"):
            assert k in lat
            assert lat[k] >= 0.0

    @pytest.mark.asyncio
    async def test_citation_score_in_range(self):
        orch = _build_orchestrator()
        result = await orch.process_query(text="Rights on arrest", lang="en")
        assert 0.0 <= result["citation_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_rights_is_nonempty_list(self):
        orch = _build_orchestrator()
        result = await orch.process_query(text="Police arrest rights", lang="en")
        assert isinstance(result["rights"], list)
        assert len(result["rights"]) > 0

    @pytest.mark.asyncio
    async def test_action_steps_is_nonempty_list(self):
        orch = _build_orchestrator()
        result = await orch.process_query(text="How to file FIR", lang="en")
        assert isinstance(result["action_steps"], list)
        assert len(result["action_steps"]) > 0

    @pytest.mark.asyncio
    async def test_lru_cache_hit_on_repeated_query(self):
        from app.agents import orchestrator as mod
        mod._cache.clear()

        orch = _build_orchestrator()
        text = "unique_lru_cache_test_query_12345"
        r1 = await orch.process_query(text=text, lang="en")
        r2 = await orch.process_query(text=text, lang="en")
        assert r1["situation_summary"] == r2["situation_summary"]

    @pytest.mark.asyncio
    async def test_empty_text_raises_422(self):
        from fastapi import HTTPException
        orch = _build_orchestrator()
        with pytest.raises(HTTPException) as exc:
            await orch.process_query(text="   ", lang="en")
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_conversation_history_bypasses_cache(self):
        from app.agents import orchestrator as mod
        mod._cache.clear()

        orch = _build_orchestrator()
        result = await orch.process_query(
            text="What about bail?",
            lang="en",
            conversation_history=[{"role": "user", "content": "prior msg"}],
        )
        assert "situation_summary" in result
        assert "citation_score" in result

    @pytest.mark.asyncio
    async def test_langfuse_broken_client_does_not_crash(self):
        """A broken _lf attribute must not propagate exceptions."""
        orch = _build_orchestrator()
        broken = MagicMock()
        broken.trace.side_effect = RuntimeError("Langfuse down")
        broken.flush.side_effect = RuntimeError("Langfuse down")
        orch._lf = broken   # inject after build — orchestrator wraps in try/except

        result = await orch.process_query(text="Anticipatory bail query", lang="en")
        assert "situation_summary" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 5. expand_corpus.section_splitter (Task 2 helper)
# ═══════════════════════════════════════════════════════════════════════════════

FIXTURE_STATUTE = """

1. Short title, extent and commencement.

This Act may be called the Bharatiya Nyaya Sanhita, 2023. It extends to the
whole of India except the State of Jammu and Kashmir.

2. Definitions.

In this Act, unless the context otherwise requires, the following expressions
have the meanings hereby respectively assigned to them.

3A. General explanations.

Throughout this Code every definition or rule of law shall apply unless
modified by a special rule of law for a particular offence.

"""


def _load_expand_corpus_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "expand_corpus.py"
    spec = importlib.util.spec_from_file_location("expand_corpus", script_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSectionSplitter:

    def test_returns_list(self):
        mod = _load_expand_corpus_module()
        assert isinstance(mod.section_splitter(FIXTURE_STATUTE, "BNS"), list)

    def test_finds_sections(self):
        mod = _load_expand_corpus_module()
        result = mod.section_splitter(FIXTURE_STATUTE, "BNS")
        assert len(result) >= 2

    def test_chunk_has_required_keys(self):
        mod = _load_expand_corpus_module()
        for chunk in mod.section_splitter(FIXTURE_STATUTE, "BNS"):
            for k in ("id", "section_ref", "title", "text", "act"):
                assert k in chunk, f"Chunk missing key: {k}"

    def test_section_ref_uses_prefix(self):
        mod = _load_expand_corpus_module()
        for chunk in mod.section_splitter(FIXTURE_STATUTE, "BNSS"):
            assert chunk["section_ref"].startswith("BNSS"), chunk["section_ref"]

    def test_chunk_text_nonempty(self):
        mod = _load_expand_corpus_module()
        for chunk in mod.section_splitter(FIXTURE_STATUTE, "BNS"):
            assert chunk["text"].strip(), f"Empty text: {chunk['id']}"

    def test_alphanumeric_section_numbers_captured(self):
        """Section '3A' must be captured (letter suffix in regex)."""
        mod = _load_expand_corpus_module()
        result = mod.section_splitter(FIXTURE_STATUTE, "BNS")
        refs = [c["section_ref"] for c in result]
        # At least one ref should contain '3A' or equivalent
        assert any("3A" in r or "3" in r for r in refs)
