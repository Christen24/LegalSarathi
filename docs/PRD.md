# Product Requirements Document (PRD): LegalSarathi

## 1. Project Overview
**Name:** LegalSarathi
**Tagline:** "Apna Kanoon, Apni Bhasha" (Your Law, Your Language)
**Vision:** To democratize legal intelligence across India by providing a high-speed, multilingual, AI-powered platform that decodes complex legal statutes, drafts documents, and connects citizens with verified local advocates.

## 2. Problem Statement
The Indian legal system is notoriously complex, slow, and inaccessible to the common citizen due to language barriers, heavy legalese, and high consultation costs. Citizens struggle to understand basic rights, file simple complaints (like RTIs or consumer disputes), or find reliable local lawyers. 

## 3. Target Audience
- **General Citizens:** Individuals seeking to understand their rights in traffic violations, consumer fraud, or tenant disputes.
- **Vulnerable Groups:** Women seeking immediate, discreet information on domestic violence or workplace harassment (POSH).
- **Small Business Owners/Startups:** Founders needing quick NDAs, lease agreements, or IP protection guidance.
- **Rural/Non-English Speakers:** Users who require legal guidance in their native vernacular languages (Hindi, Kannada, Tamil, etc.).

## 4. Core Objectives & Scope
1. **Multilingual Accessibility:** Provide highly accurate legal advice in 8+ Indian languages.
2. **Actionable Outputs:** Generate draftable documents instead of just giving generic advice.
3. **Verified Connections:** Route users to real, verified lawyers in their specific city and practice area.
4. **Performance:** Ensure the system runs efficiently on local hardware (16GB RAM constraint) using optimized vector search and asynchronous LLM pipelines.

---

## 5. Functional Requirements (Key Features)

### 5.1. Multilingual AI Legal Assistant (RAG Pipeline)
- **Description:** A conversational chatbot that grounds its answers strictly in Indian law using Retrieval-Augmented Generation (RAG).
- **Requirements:** 
  - Must accept queries in text or voice (Speech-to-Text).
  - Must translate vernacular input to English for semantic search, then translate the final response back to the user's selected language.
  - Must cite the specific legal sections (e.g., BNS, BSA, IPC, CrPC) used to generate the answer.

### 5.2. OCR Document Intelligence
- **Description:** Users can upload physical legal notices, court summons, or FIRs to understand them.
- **Requirements:**
  - Must extract text from image/PDF using local OCR (PaddleOCR).
  - The AI must summarize the document, explain the legal implications in plain language, and suggest next steps.

### 5.3. Dynamic Lawyer Directory & Routing
- **Description:** A hyperlocal directory of verified advocates.
- **Requirements:**
  - Must filter by City (Bengaluru + 10 Karnataka cities) and Practice Area (Criminal, Property, Family, Startup, etc.).
  - Must display rich metadata: Fees, experience, office address, verification badges.
  - Must integrate seamlessly with Lawrato and VakilSearch for external booking via parameterized URL generation.

### 5.4. Legal Document & RTI Wizard
- **Description:** Automated generators for common legal forms.
- **Requirements:**
  - Provide step-by-step forms for generating Anticipatory Bail Applications, Rental Agreements, and RTIs.
  - Output documents in standard, court-accepted formats ready for printing or PDF export.

### 5.5. Government Portal Tracker
- **Description:** A unified directory of official grievance portals.
- **Requirements:**
  - Direct links to Consumer Helpline, CyberCrime portal, RERA, and E-Filing systems to prevent phishing scams.

---

## 6. Non-Functional Requirements

### 6.1. Performance & Latency
- The FastAPI orchestrator must utilize `asyncio` for parallel web searching, vector retrieval, and entity extraction to ensure sub-3-second Time-To-First-Token (TTFT).
- UI must render at 60fps utilizing hardware-accelerated CSS animations (`hover-lift`, `animate-fade-in-up`).

### 6.2. UI/UX Design System ("Sarkari-Modern")
- Must convey trust and official gravity while remaining approachable.
- **Colors:** Deep Forest Teal (`#0F6E56`) and Warm Amber (`#BA7517`).
- **Typography:** Multi-script stack (Plus Jakarta Sans + Noto Sans native scripts).
- **Interactions:** Subtle glassmorphism (`glass-card`), tactile button feedback (`tap`), and progressive reveals.

### 6.3. Scalability & Deployment
- Must support fallback execution modes: Neon `pgvector` for scalable cloud deployment, and local `FAISS` indices for offline/edge computing limitations.

## 7. Future Roadmap
- **Automated Case Scraping:** Inject pipelines to fetch public High Court listings via eCourts API.
- **Pro-Bono Matching Algorithm:** Connect low-income users to legal aid clinics automatically.
- **PWA (Progressive Web App):** Enable offline legal database querying for rural internet zones.
