import os
from dotenv import load_dotenv
import urllib.parse
import json
from pathlib import Path

_p1 = Path(__file__).resolve().parents[2] / ".env"
_p2 = Path(__file__).resolve().parents[1] / ".env"
_p3 = Path.cwd() / ".env"
if _p1.exists():
    load_dotenv(dotenv_path=str(_p1), override=True)
elif _p2.exists():
    load_dotenv(dotenv_path=str(_p2), override=True)
elif _p3.exists():
    load_dotenv(dotenv_path=str(_p3), override=True)
else:
    load_dotenv(override=True)

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from app.core.config import settings
from pydantic import BaseModel
from app.agents.orchestrator import Orchestrator
from app.services.pdf_service import PDFService
from app.services.voice_service import VoiceService
from app.services.doc_service import DocService
from app.services.ocr_service import OCRService
from app.services.document_generation_service import DocumentGenerationService
from app.middleware.auth import get_optional_user
from app.services.chat_history_service import ChatHistoryService
from app.services.summarization_service import SummarizationService
from app.services.document_memory_service import DocumentMemoryService
from app.api.history import router as history_router
from jinja2 import TemplateNotFound

_chat_history_svc = ChatHistoryService()
_summarization_svc = SummarizationService()
_doc_memory_svc = DocumentMemoryService()  # embed model lazy-loaded on first use

orchestrator = None
voice_service = None
doc_service = None
ocr_service = None
document_generation_service = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, voice_service, doc_service, ocr_service, document_generation_service
    try:
        orchestrator = Orchestrator()
    except Exception as e:
        print(f"[STARTUP ERROR] Orchestrator failed to initialize: {e}")

    try:
        voice_service = VoiceService()
    except Exception as e:
        print(f"[STARTUP ERROR] VoiceService failed to initialize: {e}")

    try:
        doc_service = DocService()
    except Exception as e:
        print(f"[STARTUP ERROR] DocService failed to initialize: {e}")

    try:
        ocr_service = OCRService()
    except Exception as e:
        print(f"[STARTUP ERROR] OCRService failed to initialize: {e}")

    try:
        document_generation_service = DocumentGenerationService()
        print("[STARTUP] DocumentGenerationService initialized successfully")
    except Exception as e:
        print(f"[STARTUP ERROR] DocumentGenerationService failed to initialize: {e}")
    yield

app = FastAPI(title="Legal Sarathi 2.0 API", lifespan=lifespan)

# CORS — add FRONTEND_URL env var to allow any custom frontend origin (e.g. Vercel)
_EXTRA_ORIGIN = os.getenv("FRONTEND_URL", "").rstrip("/")
_ALLOWED_ORIGINS = [
    "http://localhost:8080",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:5173",
    "https://legal-sarathi.vercel.app",
]
if _EXTRA_ORIGIN and _EXTRA_ORIGIN not in _ALLOWED_ORIGINS:
    _ALLOWED_ORIGINS.append(_EXTRA_ORIGIN)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Transcription", "X-Query-Result"],
)

# ── Chat History Router ───────────────────────────────────────────────────────
app.include_router(history_router)

class QueryRequest(BaseModel):
    query: str
    language: str = "hi"
    session_id: str = ""        # optional: if provided, auto-save turn to this session
    pinned_history: list = []   # optional: messages from pinned session
    conversation_history: list = []  # rolling chat context for multi-turn conversation

class PDFRequest(BaseModel):
    guidance: str
    query: str = ""

class DocRequest(BaseModel):
    query: str
    doc_type: str = "RTI"
    lang: str = "en"

class TTSRequest(BaseModel):
    text: str
    lang: str = "hi"

class DocChatRequest(BaseModel):
    doc_id: str = ""           # optional: ID of a previously ingested document
    extracted_text: str
    message: str
    mode: str = "qa"           # summary|qa|clause_extract|translate|draft_reply
    history: list = []         # previous doc_chat_messages [{role, content}]
    language: str = "hi"
    session_id: str = ""       # optional: persist this turn to the session
    user_text: str = ""        # original user text (used for saving; falls back to message)

class DocumentGenerationRequest(BaseModel):
    """Request model for document generation endpoints."""
    doc_type: str  # e.g., "rti_application", "consumer_complaint"
    language: str = "english"  # Language variant of template
    data: dict  # Form data with template variables

class DocumentListResponse(BaseModel):
    """Response model for list of available documents."""
    available_documents: dict
    message: str = "Available document types and languages"



@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "legal-sarathi-2.0"}


@app.post("/api/query")
async def process_legal_query(req: QueryRequest, request: Request):
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator service is still initializing or failed to load.")

    # ── Context Retrieval (Docs + Summaries) ────────────────────────────────
    user = await get_optional_user(request)
    doc_context = ""
    session_summary = ""

    if user and user.get("user_id"):
        uid = user["user_id"]
        # Fetch document chunks relevant to the query
        doc_context = _doc_memory_svc.retrieve(
            query=req.query,
            user_id=uid,
            session_id=req.session_id if req.session_id else None,
            top_k=4
        )
        # Fetch existing long-term session summary
        if req.session_id:
            session_summary = _summarization_svc.get_summary(req.session_id)

    result = await orchestrator.process_query(
        text=req.query,
        lang=req.language,
        pinned_history=req.pinned_history if req.pinned_history else None,
        conversation_history=req.conversation_history if req.conversation_history else None,
        doc_context=doc_context,
        session_summary=session_summary,
    )

    # ── Auto-save + memory pipeline for authenticated users ─────────────────
    # Fire-and-forget: history/summary failures NEVER break the AI response.
    if req.session_id:
        try:
            user = await get_optional_user(request)
            if user and user.get("user_id"):
                uid = user["user_id"]

                # 1. Fetch session summary to inject into future prompts
                #    (already in DB from previous summarizations — no extra call needed here)

                # 2. Save this turn
                _chat_history_svc.save_turn(
                    user_id=uid,
                    session_id=req.session_id,
                    user_text=req.query,
                    ai_response=result,
                )
                print(f"[HISTORY] Saved turn to session {req.session_id}")

                # 3. Auto-title session from first message
                if not req.conversation_history:
                    _chat_history_svc.update_session_title_from_first_message(
                        user_id=uid,
                        session_id=req.session_id,
                        first_message=req.query,
                    )

                # 4. Trigger background summarizer (no-op unless threshold hit)
                _summarization_svc.maybe_summarize(
                    session_id=req.session_id,
                    language=req.language,
                )
        except Exception as e:
            print(f"[HISTORY] Auto-save failed (non-fatal): {e}")

    return result


@app.post("/api/doc-chat")
async def doc_chat(req: DocChatRequest, request: Request):
    """Document-specific chat. Bypasses RAG — extracted_text is the context."""
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator service unavailable")
    try:
        result = await orchestrator.process_doc_chat(
            doc_text=req.extracted_text,
            message=req.message,
            mode=req.mode,
            history=req.history,
            lang=req.language,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # ── Auto-save for authenticated users with an active session ─────────────
    # Fire-and-forget: failures must never break the AI response.
    if req.session_id:
        try:
            user = await get_optional_user(request)
            if user and user.get("user_id"):
                uid = user["user_id"]
                # The user_text to save: prefer explicit user_text, else use message
                save_user_text = req.user_text.strip() or req.message

                _chat_history_svc.save_turn(
                    user_id=uid,
                    session_id=req.session_id,
                    user_text=save_user_text,
                    ai_response=result,  # has 'response' key → picked up by save_turn fallback
                )
                print(f"[HISTORY] Doc-chat turn saved to session {req.session_id[:8]}")

                # Auto-title from first message (only when no prior history)
                if not req.history:
                    _chat_history_svc.update_session_title_from_first_message(
                        user_id=uid,
                        session_id=req.session_id,
                        first_message=save_user_text,
                    )
        except Exception as e:
            print(f"[HISTORY] Doc-chat auto-save failed (non-fatal): {e}")

    return result


# ── Document Memory Endpoints ─────────────────────────────────────────────────

@app.post("/api/documents/ingest")
async def ingest_document(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(default=""),
    lang: str = Form(default="en"),
):
    """
    Upload a document (PDF / image). Extracts text via OCR, chunks, embeds, and
    stores in Supabase for future RAG retrieval in this user's sessions.
    Requires a valid JWT (authenticated users only).
    """
    user = await get_optional_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Authentication required to upload documents")
    try:
        file_bytes = await file.read()
        doc_id = _doc_memory_svc.ingest(
            file_bytes=file_bytes,
            filename=file.filename or "upload",
            user_id=user["user_id"],
            session_id=session_id or None,
            lang=lang,
        )
        return {"doc_id": doc_id, "filename": file.filename, "status": "ingested"}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/user")
async def list_user_documents(request: Request, session_id: str = ""):
    """List all documents ingested by the authenticated user (optionally scoped to session)."""
    user = await get_optional_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
    docs = _doc_memory_svc.list_documents(
        user_id=user["user_id"],
        session_id=session_id or None,
    )
    return {"documents": docs}


@app.get("/api/documents/session/{session_id}")
async def get_session_document(session_id: str, request: Request):
    """Get the full raw text and filename of a document attached to a specific chat session."""
    user = await get_optional_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        from app.services.supabase_service import get_supabase_admin
        sb = get_supabase_admin()
        q = (
            sb.table("user_documents")
            .select("filename, raw_text")
            .eq("user_id", user["user_id"])
            .eq("session_id", session_id)
            .limit(1)
        )
        res = q.execute().data
        if res:
            return res[0]
        return {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents/{doc_id}")
async def delete_user_document(doc_id: str, request: Request):
    """Delete a user document and all its chunks."""
    user = await get_optional_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
    deleted = _doc_memory_svc.delete_document(doc_id, user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found or access denied")
    return {"deleted": True, "doc_id": doc_id}


# ── OCR Endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/ocr-extract")
async def ocr_extract(
    image: UploadFile = File(...),
    lang: str = Form(default="hi"),
):
    """
    OCR-only endpoint. Accepts image or PDF, returns extracted text.
    Frontend uses this to show a text preview before submitting to /api/ocr-query.
    """
    try:
        if not ocr_service:
            raise HTTPException(status_code=503, detail="OCR service is unavailable.")
        file_bytes = await image.read()
        filename = image.filename or ""
        extracted = ocr_service.extract(file_bytes, filename, lang)
        if not extracted:
            raise HTTPException(status_code=422, detail="Could not extract text from file.")
        return JSONResponse({"extracted_text": extracted, "char_count": len(extracted)})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ocr-query")
async def ocr_query(
    image: UploadFile = File(...),
    lang: str = Form(default="hi"),
):
    """
    Full pipeline: OCR image/PDF → extract text → run legal RAG pipeline.
    Returns the same structure as /api/query.
    """
    try:
        if not ocr_service or not orchestrator:
            raise HTTPException(status_code=503, detail="OCR or Orchestrator service is unavailable.")
        file_bytes = await image.read()
        filename = image.filename or ""
        extracted = ocr_service.extract(file_bytes, filename, lang)
        if not extracted:
            raise HTTPException(
                status_code=422,
                detail="Could not extract text from the uploaded file."
            )
        print(f"[OCR-QUERY] Extracted {len(extracted)} chars, routing to orchestrator (lang={lang})")
        result = await orchestrator.process_query(text=extracted, lang=lang)
        result["ocr_source"] = filename
        result["ocr_extracted_text"] = extracted[:500]   # preview for UI
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Voice Endpoints ───────────────────────────────────────────────────────────

@app.post("/api/tts")
async def text_to_speech(req: TTSRequest, background_tasks: BackgroundTasks):
    """Standalone TTS — convert any text to mp3 in given lang. Used for replay."""
    try:
        if not voice_service:
            raise HTTPException(status_code=503, detail="Voice service is unavailable.")
        mp3_path = await voice_service.synthesize(req.text, lang=req.lang)
        background_tasks.add_task(os.unlink, mp3_path)
        return FileResponse(
            mp3_path,
            media_type="audio/mpeg",
            filename="buddy_response.mp3"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/voice-query")
async def voice_query(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    lang: str = Form(default="hi")
):
    """Voice-in / Voice-out: accept audio (any format), return mp3 response."""
    try:
        if not voice_service or not orchestrator:
            raise HTTPException(status_code=503, detail="Voice or Orchestrator service is unavailable.")
        audio_bytes = await audio.read()
        # Groq Whisper accepts WebM/Opus/WAV directly — no conversion needed
        transcribed = voice_service.transcribe(audio_bytes, lang=lang)
        if not transcribed:
            raise HTTPException(status_code=422, detail="Could not understand audio.")

        result = await orchestrator.process_query(text=transcribed, lang=lang)
        buddy_text = result.get("buddy_text", "No guidance available.")

        mp3_path = await voice_service.synthesize(buddy_text, lang=lang)
        background_tasks.add_task(os.unlink, mp3_path)
        return FileResponse(
            mp3_path,
            media_type="audio/mpeg",
            filename="response.mp3",
            headers={
                "X-Transcription": urllib.parse.quote(transcribed[:200]),
                "X-Query-Result": json.dumps(result, ensure_ascii=True)
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ── Document Endpoints ────────────────────────────────────────────────────────

@app.post("/api/download-pdf")
async def download_pdf(req: PDFRequest):
    try:
        pdf_buffer = await PDFService.generate_draft(req.guidance, req.query)
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=legal_draft.pdf"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-draft-pdf")
async def generate_draft_pdf(req: DocRequest):
    """Generates an RTI/FIR/BAIL draft using Groq and returns it as a PDF."""
    try:
        if not doc_service:
            raise HTTPException(status_code=503, detail="Doc service is unavailable.")
        draft_text = await doc_service.generate_document(req.query, req.doc_type, req.lang)
        pdf_buffer = await PDFService.generate_draft(draft_text, f"{req.doc_type} Draft: {req.query}")
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={req.doc_type.lower()}_draft.pdf"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Template Document Generation Endpoints ─────────────────────────────────────

@app.get("/api/documents/available")
async def get_available_documents():
    """Get list of all available document types and their language variants."""
    if not document_generation_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    try:
        available = document_generation_service.get_available_templates()
        return {"available_documents": available, "message": "Success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{doc_type}/fields")
async def get_document_fields(doc_type: str, language: str = "english"):
    """Get required form fields for a specific document type."""
    if not document_generation_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    try:
        if not document_generation_service.validate_document_type(doc_type):
            raise HTTPException(status_code=404, detail="Document type not found")
        fields = document_generation_service.get_required_fields(doc_type, language)
        info = document_generation_service.get_document_info(doc_type)
        return {"doc_type": doc_type, "language": language, "required_fields": fields, "document_info": info}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/render")
async def render_document(req: DocumentGenerationRequest):
    """Render a legal document template with the provided data (HTML preview)."""
    if not document_generation_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    try:
        if not document_generation_service.validate_document_type(req.doc_type):
            raise HTTPException(status_code=400, detail="Invalid doc_type")
        html = document_generation_service.render_document(req.doc_type, req.language, req.data)
        return {"status": "success", "html": html}
    except TemplateNotFound: raise HTTPException(status_code=404, detail="Template not found")
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/generate-pdf")
async def generate_template_pdf(req: DocumentGenerationRequest):
    """Render a document template and convert it to PDF."""
    if not document_generation_service:
        raise HTTPException(status_code=503, detail="Service not initialized")
    try:
        html = document_generation_service.render_document(req.doc_type, req.language, req.data)
        pdf_buffer = await PDFService.generate_draft(html, f"{req.doc_type} - {req.language}")
        filename = f"{req.doc_type}_{req.language}.pdf"
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
