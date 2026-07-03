# LegalSarathi - System Architecture

This document details the High-Level Design (HLD) and Low-Level Design (LLD) for the LegalSarathi platform.

## 1. High-Level Design (HLD)

The system follows a client-server architecture. The Next.js frontend handles multimodal input (text, voice, image/PDF) and streams requests to the FastAPI backend. The backend acts as an orchestrator, distributing workloads across local ML models and fast external LLM APIs (Groq).

```mermaid
graph TD
    User((User)) -->|Text / Voice / Image| Frontend[Next.js Frontend]
    Frontend -->|HTTP / REST| API[FastAPI Entrypoint]
    
    subgraph "Backend Core"
        API --> Orchestrator[Orchestrator Engine]
        API --> Voice[VoiceService]
        API --> OCR[OCRService]
        API --> PDF[PDFService]
        API --> Doc[DocService]
    end
    
    subgraph "Intelligence Pipeline"
        Orchestrator --> Translate[TranslatorService]
        Orchestrator --> Parallel[Parallel Execution Pool]
        
        Parallel --> Groq[GroqService]
        Parallel --> Search[SearchService]
        Parallel --> RAG[RAGService]
        Parallel --> Specialist[SpecialistService]
        
        Orchestrator --> Rerank[RerankerService]
        Orchestrator --> Audit[CitationAuditService]
    end
    
    Groq -.->|Llama-3| GroqAPI[Groq Cloud API]
    RAG -.->|Vector Search| DB[(FAISS / Neon pgvector)]
    OCR -.-> Paddle[Local PaddleOCR]
    Specialist -.-> LocalLLM[Local GGUF Model]
    Voice -.-> Whisper[Groq Whisper STT]
```

### Component Breakdown
- **Next.js Frontend**: "Sarkari-Modern" UI. Handles media capture and state.
- **FastAPI**: Asynchronous API server.
- **Orchestrator**: The central brain. Manages the workflow, caching, and parallel execution.
- **RAGService + RerankerService**: Two-stage retrieval. `RAGService` fetches broad candidates; `RerankerService` (CrossEncoder) scores and sorts them by semantic relevance.
- **CitationAuditService**: Post-generation step to verify LLM claims against retrieved chunks.
- **OCRService**: Uses PaddleOCR for extracting text from legal documents before analysis.

---

## 2. Low-Level Design (LLD)

The LLD focuses on the core `process_query` flow within the `Orchestrator`. It is designed for maximum speed by aggressively parallelizing I/O-bound and local compute tasks.

```mermaid
sequenceDiagram
    participant F as Frontend
    participant O as Orchestrator
    participant C as LRU Cache
    participant T as Translator
    participant P as Parallel Task Pool
    participant G as GroqService
    participant R as RAGService
    participant S as SearchService
    participant RR as RerankerService
    participant A as CitationAuditService

    F->>O: process_query(text, lang)
    O->>C: Check cache (hash of text+lang)
    
    alt Cache Hit
        C-->>O: Return cached result
        O-->>F: JSON Response
    else Cache Miss
        O->>T: translate_to_english(text, lang)
        T-->>O: english_text
        
        Note over O, P: Parallel Phase minimizes latency
        O->>P: Dispatch Tasks
        par Extract Keys
            P->>G: extract_legal_keys(english_text)
        and Web Search
            P->>S: search_legal_context(keywords)
        and RAG Retrieval
            P->>R: retrieve_hybrid(english_text)
        end
        P-->>O: keys, web_context, rag_chunks_raw
        
        O->>RR: rerank(english_text, rag_chunks_raw, top_k=5)
        RR-->>O: top_rag_chunks
        
        O->>G: synthesize_buddy_response(..., target_lang)
        G-->>O: buddy_data (Summary, Rights, Actions)
        
        O->>A: audit(full_answer_text, top_rag_chunks)
        A-->>O: citation_score, badge
        
        O->>C: Store result in LRU Cache
        O-->>F: Final JSON Response
    end
```

### Execution Details
1. **Caching**: Memory-based LRU cache prevents redundant processing for repeated queries.
2. **Translation**: All internal reasoning is done in English to maximize LLM performance. Output is translated back at synthesis.
3. **Parallel Task Pool**: Key extraction, web search, hybrid RAG, and optional local GGUF execution run concurrently using `asyncio.gather`.
4. **Re-ranking**: Raw chunks from FAISS/pgvector are passed through a CrossEncoder for high-precision ordering.
5. **Synthesis**: Groq builds structured JSON (buddy_data) containing situation, rights, actions, and constraints.
6. **Audit**: System assigns a trust badge based on how closely the synthesized response matches the source text.
