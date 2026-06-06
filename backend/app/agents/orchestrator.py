"""
backend/app/agents/orchestrator.py

Async parallel orchestrator with LRU cache + optional Langfuse tracing.

Langfuse is completely optional:
  - If LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are absent → self._lf = None
  - Every Langfuse call is wrapped in try/except → a Langfuse failure NEVER
    breaks a query or raises an unhandled exception.

Existing process_query() signature is preserved exactly.
"""

import os
import time
import asyncio
import hashlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from app.services.groq_service import GroqService
from app.services.specialist_service import SpecialistService
from app.services.translator import TranslatorService
from app.services.search_service import SearchService
from app.services.rag_service import RAGService
from app.services.citation_audit import CitationAuditService
from app.services.reranker_service import RerankerService

# Single-thread executor for GGUF (llama_cpp not thread-safe)
_GGUF_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gguf")

HELP_CHANNELS_BASE = [
    {"name": "NALSA",            "phone": "15100",        "url": "https://nalsa.gov.in"},
    {"name": "RTI Portal",       "phone": None,           "url": "https://rtionline.gov.in"},
    {"name": "Women Helpline",   "phone": "1091",         "url": "https://wcd.nic.in"},
    {"name": "Supreme Court Aid","phone": None,           "url": "https://sci.gov.in"},
    {"name": "Consumer Forum",   "phone": "1800-11-4000", "url": "https://consumerhelpline.gov.in"},
    {"name": "Police",           "phone": "100",          "url": "https://digitalpolice.gov.in"},
]

# ── In-memory LRU cache (no Redis needed) ─────────────────────────────────────
_CACHE_MAX = 100
_CACHE_TTL = 3600          # seconds (1 hour)
_cache: OrderedDict = OrderedDict()


def _cache_key(text: str, lang: str) -> str:
    return hashlib.sha256(f"{text.strip().lower()}::{lang}".encode()).hexdigest()[:16]


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, val = entry
    if time.time() - ts > _CACHE_TTL:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return val


def _cache_set(key: str, value: dict):
    if key in _cache:
        _cache.move_to_end(key)
    _cache[key] = (time.time(), value)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


# ── Langfuse helpers ───────────────────────────────────────────────────────────

def _lf_span(trace_or_span, name: str, **kwargs):
    """
    Safe wrapper: create a Langfuse span. Returns the span object or None.
    Never raises.
    """
    try:
        if trace_or_span is None:
            return None
        return trace_or_span.span(name=name, **kwargs)
    except Exception as exc:
        print(f"[LANGFUSE] span({name}) error (non-fatal): {exc}")
        return None


def _lf_end(span, **kwargs):
    """Safe wrapper: end a Langfuse span. Never raises."""
    try:
        if span is not None:
            span.end(**kwargs)
    except Exception as exc:
        print(f"[LANGFUSE] span.end error (non-fatal): {exc}")


def _lf_generation(span, name: str, model: str, input_text: str,
                   output, usage: dict):
    """Safe wrapper: create a Langfuse generation event inside a span. Never raises."""
    try:
        if span is None:
            return
        span.generation(
            name=name,
            model=model,
            input=input_text,
            output=output,
            usage=usage,
        )
    except Exception as exc:
        print(f"[LANGFUSE] generation({name}) error (non-fatal): {exc}")


def _lf_score(trace, name: str, value: float):
    """Safe wrapper: add a score to a Langfuse trace. Never raises."""
    try:
        if trace is None:
            return
        trace.score(name=name, value=value)
    except Exception as exc:
        print(f"[LANGFUSE] score({name}) error (non-fatal): {exc}")


# ═══════════════════════════════════════════════════════════════════════════════

class Orchestrator:
    def __init__(self):
        self.groq_service    = GroqService()
        self.translator      = TranslatorService()
        self.search_service  = SearchService()
        self.citation_audit  = CitationAuditService()
        self.reranker        = RerankerService()

        try:
            self.specialist_service = SpecialistService()
        except Exception as e:
            print(f"[GGUF API] Offline: {e}")
            self.specialist_service = None

        # RAG loads at startup — non-fatal if index not built yet
        self.rag_service = RAGService()
        if not self.rag_service.is_ready:
            print("[RAG] Not ready — run ingest_corpus.py to build index")

        # ── Optional Langfuse client ───────────────────────────────────────────
        self._lf = None
        try:
            pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
            sk = os.getenv("LANGFUSE_SECRET_KEY", "")
            if pk and sk:
                import langfuse
                host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
                self._lf = langfuse.Langfuse(
                    public_key=pk,
                    secret_key=sk,
                    host=host,
                )
                print("[LANGFUSE] Client initialized OK")
            else:
                print("[LANGFUSE] Keys not set — tracing disabled")
        except ImportError:
            print("[LANGFUSE] Package not installed — tracing disabled")
        except Exception as exc:
            print(f"[LANGFUSE] Init failed (non-fatal): {exc}")

    # ──────────────────────────────────────────────────────────────────────────
    # process_query — signature UNCHANGED
    # ──────────────────────────────────────────────────────────────────────────

    async def process_query(
        self,
        text: str,
        lang: str = "hi",
        pinned_history: list = None,
        conversation_history: list = None,
        doc_context: str = "",
        session_summary: str = "",
    ):
        if not text or not text.strip():
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="Query cannot be empty.")

        # ── Pinned History Context Injection ───────────────────────────────────
        prefix_context = []
        if pinned_history:
            pinned_summary = "\n".join(
                [f"{m['role']}: {m['content'][:200]}" for m in pinned_history[-5:]]
            )
            prefix_context.append(f"CONTEXT FROM PINNED SESSION:\n{pinned_summary}")

        if session_summary:
            prefix_context.append(f"EARLIER IN THIS SESSION:\n{session_summary}")

        if prefix_context:
            text = "\n\n".join(prefix_context) + f"\n\nUSER CURRENT QUERY: {text}"

        # ── LRU Cache check ────────────────────────────────────────────────────
        cache_key = _cache_key(text, lang)
        if not conversation_history:
            cached = _cache_get(cache_key)
            if cached is not None:
                print(f"[CACHE HIT] key={cache_key}")
                return cached
        else:
            print(f"[CACHE SKIP] conversation_history present ({len(conversation_history)} turns)")

        start_total = time.time()

        # ── Langfuse trace ─────────────────────────────────────────────────────
        lf_trace = None
        try:
            if self._lf is not None:
                lf_trace = self._lf.trace(
                    name="legal_sarathi_query",
                    input=text,
                    metadata={
                        "language":   lang,
                        "session_id": cache_key,
                    },
                )
        except Exception as exc:
            print(f"[LANGFUSE] trace() failed (non-fatal): {exc}")

        # ── 1. Translate to English ────────────────────────────────────────────
        t0 = time.time()
        lf_span_translate = _lf_span(lf_trace, "translation")
        try:
            english_text = await self.translator.translate_to_english(text, lang)
        finally:
            _lf_end(lf_span_translate, output=locals().get("english_text", ""))
        t_translate = time.time() - t0
        print(f"[LATENCY] Translation: {t_translate:.3f}s")

        # ── 2. Parallel: Groq key extraction + Web search + Hybrid RAG ────────
        t0 = time.time()
        seed_keywords = [w for w in english_text.split() if len(w) > 3][:5]

        parallel_tasks = [
            self.groq_service.extract_legal_keys(english_text),
            asyncio.to_thread(self.search_service.search_legal_context, seed_keywords),
        ]
        if self.rag_service.is_ready:
            parallel_tasks.append(
                asyncio.to_thread(self.rag_service.retrieve_hybrid, english_text)
            )

        if self.specialist_service:
            parallel_tasks.append(
                self.specialist_service.generate_guidance(english_text, seed_keywords)
            )

        lf_span_parallel = _lf_span(lf_trace, "parallel_retrieval")
        results = await asyncio.gather(*parallel_tasks)
        legal_keys = results[0]
        web_context, source_urls = results[1]

        _result_idx = 2

        if self.rag_service.is_ready:
            rag_chunks_raw, t_rag = results[_result_idx]
            _result_idx += 1
        else:
            rag_chunks_raw, t_rag = [], 0.0

        if self.specialist_service:
            gguf_result = results[_result_idx]
            _result_idx += 1
        else:
            gguf_result = ""

        _lf_end(
            lf_span_parallel,
            metadata={"rag_candidates": len(rag_chunks_raw)},
        )

        # Fallback web search if seed returned nothing
        if (not web_context.strip() or web_context == "No legal context found.") and legal_keys:
            web_context, source_urls = await asyncio.to_thread(
                self.search_service.search_legal_context, legal_keys
            )

        t_parallel = time.time() - t0
        print(
            f"[LATENCY] Parallel (keys+search+hybrid RAG): {t_parallel:.3f}s"
            f" | Candidates: {len(rag_chunks_raw)}"
        )

        # ── 3. CrossEncoder Re-Ranking ─────────────────────────────────────────
        t0 = time.time()
        lf_span_rerank = _lf_span(
            lf_trace, "reranking",
            metadata={"chunks_in": len(rag_chunks_raw)},
        )
        if rag_chunks_raw:
            rag_chunks = self.reranker.rerank(english_text, rag_chunks_raw, top_k=5)
        else:
            rag_chunks = rag_chunks_raw
        _lf_end(
            lf_span_rerank,
            metadata={"chunks_in": len(rag_chunks_raw), "chunks_out": len(rag_chunks)},
        )
        t_rerank = time.time() - t0
        print(f"[LATENCY] Re-ranking: {t_rerank:.3f}s | Final chunks: {len(rag_chunks)}")

        # ── 4. Format RAG for Groq ─────────────────────────────────────────────
        rag_context = self.rag_service.format_for_prompt(rag_chunks) if rag_chunks else ""
        print(f"[RAG] Top sections: {[c.get('section_ref', 'UNKNOWN') for c in rag_chunks[:3]]}")
        print(f"[GGUF API] Result chars: {len(gguf_result)}")

        # ── 5. Groq synthesis ──────────────────────────────────────────────────
        t0 = time.time()
        lf_span_groq = _lf_span(
            lf_trace, "groq_synthesis",
            metadata={"model": "llama-3.3-70b-versatile"},
        )

        buddy_data = await self.groq_service.synthesize_buddy_response(
            english_text=english_text,
            legal_keys=legal_keys,
            web_context=web_context,
            target_lang=lang,
            specialist_opinion=gguf_result,
            rag_context=rag_context,
            conversation_history=conversation_history or [],
            doc_context=doc_context,
        )

        # Attempt to capture token usage from the last Groq completion.
        # groq_service stores the raw completion on self.groq_service._last_completion
        # if present; otherwise usage is empty dict.
        usage_dict: dict = {}
        try:
            last_completion = getattr(self.groq_service, "_last_completion", None)
            if last_completion and hasattr(last_completion, "usage") and last_completion.usage:
                u = last_completion.usage
                usage_dict = {
                    "promptTokens":     getattr(u, "prompt_tokens", None),
                    "completionTokens": getattr(u, "completion_tokens", None),
                    "totalTokens":      getattr(u, "total_tokens", None),
                }
        except Exception:
            pass

        _lf_generation(
            lf_span_groq,
            name="groq_synthesis",
            model="llama-3.3-70b-versatile",
            input_text=english_text,
            output=buddy_data,
            usage=usage_dict,
        )
        _lf_end(lf_span_groq)
        t_synthesis = time.time() - t0
        print(f"[LATENCY] Groq Synthesis: {t_synthesis:.3f}s")

        # ── 6. Citation Audit ──────────────────────────────────────────────────
        full_answer_text = " ".join([
            buddy_data.get("situation_summary", ""),
            " ".join(buddy_data.get("rights", [])),
            " ".join(buddy_data.get("action_steps", [])),
            buddy_data.get("awareness", ""),
        ])

        lf_span_audit = _lf_span(lf_trace, "citation_audit")
        citation_result = self.citation_audit.audit(full_answer_text, rag_chunks)
        _lf_end(
            lf_span_audit,
            metadata={"citation_score": citation_result["citation_score"]},
        )
        print(
            f"[CITATION] Score={citation_result['citation_score']} | "
            f"{citation_result['badge'].encode('ascii', 'ignore').decode()}"
        )

        # Add Langfuse quality score for the trace
        _lf_score(lf_trace, "citation_quality", citation_result["citation_score"])

        # Flush Langfuse buffer (non-blocking best-effort)
        try:
            if self._lf is not None:
                self._lf.flush()
        except Exception as exc:
            print(f"[LANGFUSE] flush() error (non-fatal): {exc}")

        # ── Assemble final result ──────────────────────────────────────────────
        groq_channels  = buddy_data.get("help_channels", [])
        existing_names = {c["name"] for c in groq_channels}
        for ch in HELP_CHANNELS_BASE:
            if ch["name"] not in existing_names:
                groq_channels.append(ch)
        buddy_data["help_channels"] = groq_channels[:6]
        buddy_data["source_urls"]   = source_urls

        total_time = time.time() - start_total
        print(f"[LATENCY] Total: {total_time:.3f}s")

        query_in_target_lang = text
        if lang not in ("en", "en-IN"):
            try:
                query_in_target_lang = await self.translator.translate(
                    text, source_lang="auto", target_lang=lang
                )
            except Exception as e:
                print(f"[TRANSLATE ERROR] query target lang failed: {e}")

        result = {
            "query":             text,
            "query_in_target_lang": query_in_target_lang,
            "lang":              lang,
            "legal_keys":        legal_keys,
            "gguf_raw":          gguf_result[:800] if gguf_result else "",
            "rag_chunks_used":   [c.get("section_ref", "UNKNOWN") for c in rag_chunks],
            "citation_score":    citation_result["citation_score"],
            "citation_badge":    citation_result["badge"],
            "situation_summary": buddy_data.get("situation_summary", ""),
            "severity_level":    buddy_data.get("severity_level", "INFO"),
            "rights":            buddy_data.get("rights", []),
            "action_steps":      buddy_data.get("action_steps", []),
            "do_not_do":         buddy_data.get("do_not_do", []),
            "evidence_required": buddy_data.get("evidence_required", []),
            "jurisdiction_note": buddy_data.get("jurisdiction_note", ""),
            "awareness":         buddy_data.get("awareness", ""),
            "buddy_text":        buddy_data.get("buddy_text", ""),
            "help_channels":     buddy_data.get("help_channels", []),
            "source_urls":       buddy_data.get("source_urls", []),
            "latency": {
                "translation": round(t_translate, 2),
                "parallel":    round(t_parallel, 2),
                "rerank":      round(t_rerank, 2),
                "synthesis":   round(t_synthesis, 2),
                "total":       round(total_time, 2),
            },
        }

        # Store in LRU cache (only for single-turn queries)
        if not conversation_history:
            _cache_set(cache_key, result)
        return result

    # ── Document Chat ──────────────────────────────────────────────────────────

    async def process_doc_chat(
        self,
        doc_text: str,
        message: str,
        mode: str,
        history: list,
        lang: str,
    ) -> dict:
        """
        Document-specific chat — bypasses RAG entirely.
        The extracted document text IS the retrieval context.
        """
        try:
            english_message = await self.translator.translate_to_english(message, lang)
        except Exception:
            english_message = message

        doc_context = doc_text[:4000]

        history_str = ""
        for msg in history[-6:]:
            label = "User" if msg.get("role") == "user" else "Assistant"
            history_str += f"{label}: {msg.get('content', '')[:300]}\n"

        response = await self.groq_service.doc_chat_response(
            doc_context=doc_context,
            message=english_message,
            mode=mode,
            history=history_str,
            target_lang=lang,
        )

        return {
            "mode":          mode,
            "response":      response,
            "doc_chars_used": len(doc_context),
        }
