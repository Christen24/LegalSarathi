# Local Setup Guide — LegalSarathi

Follow these steps to run the LegalSarathi platform on your 16GB CPU machine.

## Prerequisites
- **Python 3.9+**
- **Node.js 18+**
- **Poppler** (Required for PDF OCR)
  - Windows: `conda install -c conda-forge poppler` or download binaries and add to PATH.

---

## 1. Environment Configuration
Copy the `.env.example` file to `.env` and provide your API keys.

```powershell
cp .env.example .env
```

**Required Keys:**
- `GROQ_API_KEY`: Get a free key from [console.groq.com](https://console.groq.com).
- `NEON_DATABASE_URL`: (Optional) If you want to use the cloud pgvector database. Otherwise, the system defaults to local FAISS.

---

## 2. Backend Setup
The backend uses FastAPI and several local ML models (shipped as lazy-loaded packages).

```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser (Required for the new PDF engine)
playwright install chromium
```

---

## 3. Frontend Setup
The frontend is a high-performance Vite + React application.

```powershell
cd frontend
npm install
```

---

## 4. Running the Platform
You need to run both the backend and the frontend simultaneously.

### Terminal 1: Backend
```powershell
cd backend
.\venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

### Terminal 2: Frontend
```powershell
cd frontend
npm run dev
```

The application will be available at: **http://localhost:8080**

---

## 💡 Performance Notes for 16GB Machine
- **⚡ First OCR Upload**: The system uses **PaddleOCR**. It will download ~80-100MB of model data for the selected language on first use. This may take 30-60 seconds.
- **🔍 First Rerank**: The CrossEncoder model (23MB) will load on the first query.
- **💾 Memory Usage**: The system is highly optimized for CPU. The idle system uses ~500MB. During OCR+RAG processing, it may spike to 2GB total, well within your 16GB limit.

