# LegalSarathi: Comprehensive Project Documentation (A to Z)

This document provides a holistic, deep-dive explanation of the entire LegalSarathi ecosystem, detailing the architecture, technology stack, datasets, and API integrations that power the platform.

---

## 1. Architectural Overview

Legal Sarathi operates on a decoupled client-server architecture, specifically optimized to run complex AI Retrieval-Augmented Generation (RAG) pipelines on constrained local hardware (16GB RAM) while supporting infinite cloud scalability.

### **1.1. Frontend (Client Layer)**
- **Framework:** React 18 powered by Vite. Selected for sub-100ms Hot Module Replacement (HMR) and optimized production bundling.
- **Routing:** `react-router-dom` handling complex state transfers (e.g., passing Voice/Text intents from Home directly to Chat).
- **Styling:** Vanilla Tailwind CSS implementing the bespoke **"Sarkari-Modern"** design system.
  - *Sarkari-Modern Logic:* Utilizes official-feeling color tokens (Deep Forest Teal `#0F6E56` & Warm Amber `#BA7517`), paired with multi-script typography (`Plus Jakarta Sans` for English, `Noto Sans` for vernacular Indian scripts).
  - *Micro-interactions:* Global CSS utilities like `.hover-lift` (physics-based card hover), `.glass-card` (backdrop blurs), and `.animate-fade-in-up` (staggered list reveals).
- **Context Management:** Strict separation of concerns via custom React Contexts (`AuthContext`, `LanguageContext`, `GuestContext`) handling local storage persistence.

### **1.2. Backend (Orchestration Layer)**
- **Framework:** FastAPI (Python). Chosen for its native asynchronous capabilities (`asyncio`), allowing parallel execution of heavy I/O tasks.
- **Server:** Uvicorn ASGI server.
- **Data Flow Pipeline:**
  1. **Reception & Translation:** Accepts multimodal input. Translates non-English queries to English to maintain high-fidelity embedding matching.
  2. **Parallel Retrieval:** Simultaneously dispatches searches to the local Vector Database and live Web Search tools.
  3. **Re-Ranking:** Retrieved chunks are fed through a CrossEncoder to score contextual relevance against the original query.
  4. **Synthesis:** The highly-ranked context is injected into a Groq-powered LLM prompt for final generation.

---

## 2. Technology & AI Stack

### **2.1. Core AI Models**
- **LLM Engine:** **Groq API** utilizing the `Llama-3-70B-8192` model. Selected for its ultra-low latency inference, crucial for conversational flow.
- **Embedding Model:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Creates semantic vector representations of Indian legal text.
- **Re-ranker:** CrossEncoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`). Evaluates the semantic similarity between the user's query and the retrieved database chunks to eliminate hallucinations.
- **Speech-to-Text (STT):** **Groq Whisper API**. Handles native voice inputs with robust Indian accent recognition.
- **Vision/OCR:** **PaddleOCR**. A localized, lightweight optical character recognition engine used to extract text from user-uploaded PDFs and images (FIRs, legal notices).

### **2.2. Database & Retrieval**
- **Vector Database (Cloud Primary):** **Neon pgvector** (PostgreSQL extension for vector similarity search).
- **Vector Database (Local Fallback):** **FAISS** (Facebook AI Similarity Search). Enables the platform to operate entirely offline without cloud database costs.

---

## 3. Datasets & Knowledge Base

Legal Sarathi’s accuracy relies on high-quality, curated datasets injected into its vector stores and frontend arrays.

### **3.1. RAG Knowledge Base (Backend)**
Stored in the backend `knowledge_base/` directory and embedded into the vector index:
- **Bharatiya Nyaya Sanhita (BNS) & IPC:** Criminal codes.
- **Bharatiya Sakshya Adhiniyam (BSA) & Evidence Act.**
- **Consumer Protection Act, 2019:** Rights against fraud and service deficiency.
- **Information Technology Act, 2000:** Cybercrime regulations.
- **POSH Act & Domestic Violence Act:** Women's safety guidelines.

### **3.2. Lawyer Directory Datasets (Frontend)**
Curated JSON datasets powering the hyperlocal advocate discovery features in `frontend/src/data/`:
- **`bangalore_lawyers_100plus.json`:** Contains 100+ highly detailed profiles of advocates in Bengaluru, complete with specific neighborhoods, ratings, and localized practice highlights.
- **`karnataka_10cities_lawyers.json`:** Contains 120 profiles expanding coverage across Mysuru, Hubballi-Dharwad, Mangaluru, Belagavi, Kalaburagi, Davanagere, Ballari, Shivamogga, and Tumakuru. 
- *Dynamic Mapping:* The frontend `lawyers.ts` script dynamically ingests these JSONs, normalizes the city strings to standardized slugs (e.g., "Mysuru" -> `mysore`), and categorizes the advocates via string-matching algorithms targeting practice fields (e.g., finding "revenue" assigns the `Property` tag).

---

## 4. API Calls & Third-Party Integrations

### **4.1. Internal API Endpoints (FastAPI)**
- `POST /api/chat/message`: The primary RAG orchestrator endpoint. Accepts JSON containing `query`, `history`, and `language`. Returns the synthesized legal advice and source citations.
- `POST /api/chat/audio`: Accepts multipart form-data `.webm` or `.wav` blobs, proxies to Groq Whisper, and returns the transcribed text.
- `POST /api/analyze/document`: Accepts PDF/Image uploads, executes PaddleOCR, and streams back the legal summary.

### **4.2. External API Invocations**
- **Groq API (`https://api.groq.com/openai/v1/chat/completions`):** Executed securely from the backend to prevent key exposure. Powers the core cognitive generation.
- **Groq Whisper API (`https://api.groq.com/openai/v1/audio/transcriptions`):** Audio to text transcription.

### **4.3. Third-Party Platform Integrations (Frontend URL Routing)**
Instead of building a massive booking system from scratch, Legal Sarathi acts as an intelligent router to existing infrastructure:
- **Lawrato Integration:** Dynamically constructs URLs based on city and practice area.
  - *Example Logic:* `https://lawrato.com/lawyers/${citySlug}/${categorySlug}-lawyers`
- **VakilSearch Integration:** Similar routing parameterization for corporate/startup legal services.
- **Government Portals:** Hardcoded, verified links to eDaakhil (Consumer), CyberCrime.gov.in, and state RERA portals to prevent users from falling victim to SEO phishing sites.

---

## 5. Summary of Key Workflows

1. **The Voice/Text Query Flow:**
   User speaks Kannada into the UI -> React captures `.webm` -> FastAPI sends to Groq Whisper -> Kannada text is translated to English -> FAISS retrieves English BNS laws -> Groq synthesizes advice -> Translated back to Kannada -> Displayed in UI.
2. **The Lawyer Discovery Flow:**
   User navigates to Lawyers tab -> Selects "Mysuru" and "Property" -> `Lawyers.tsx` filters the `LAWYERS` array (ingested from `karnataka_10cities_lawyers.json`) -> UI renders `.glass-card` profiles with `.hover-lift` physics -> User clicks "Website" or "Map" for direct routing.
3. **The Document Generation Flow:**
   User selects "Rent Agreement" -> UI provides a step-by-step form -> React constructs a localized markdown template -> Prompts user to print or save to PDF.
