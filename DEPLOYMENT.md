# LegalSarathi 2.0 — DevOps Deployment Guide

## PART 1: Security Vulnerabilities Audit

---

### 🔴 CRITICAL — Must Fix Before Any Public Deployment

#### 1. Real API Keys Committed to GitHub (via `frontend/.env`)
**File:** `frontend/.env`
```
VITE_SUPABASE_ANON_KEY=<your_supabase_anon_key>
VITE_SUPABASE_URL=https://<your_project_ref>.supabase.co
```
**Risk:** These are committed in the repository. Anyone who clones the repo has your Supabase project key. While `anon` keys have Row Level Security (RLS) gating them, they can still be used to probe your database schema, attempt auth flows, or hit rate limits. The Supabase URL is also the identifier for your project, enabling enumeration attacks.

**Fix:** Verify `frontend/.env` is in `.gitignore` (it is ✅) and run a git secret scan to confirm it was never committed:
```bash
git log --all -p -- "frontend/.env"  # should return nothing
```
For deployment, inject these as environment variables on your hosting platform, never in committed files.

---

#### 2. Backend `.env` Contains Service Role Key (Highest Privilege Supabase Key)
**File:** `.env` (root)
```
SUPABASE_SERVICE_ROLE_KEY=<your_service_role_key>
SUPABASE_JWT_SECRET=<your_jwt_secret>
GROQ_API_KEY=<your_groq_api_key>
TAVILY_API_KEY=<your_tavily_key>
```
The `SUPABASE_SERVICE_ROLE_KEY` **bypasses ALL Row Level Security policies**. If this key is exposed, an attacker has full admin access to your database. The Groq key, if leaked, can be used to bill your account indefinitely.

**Fix:**
- Root `.env` is gitignored ✅ — confirmed not committed.
- **Immediately rotate ALL keys** in the respective dashboards (Groq, Supabase, Tavily) before deploying, as these may have been shared/visible during development.
- Never log or print these values in backend responses.

---

#### 3. No Rate Limiting on AI Endpoints
**File:** `backend/app/main.py`

All AI endpoints (`/api/query`, `/api/voice-query`, `/api/ocr-query`) have **zero rate limiting**. Each call hits Groq's paid API. A malicious actor or bot can make thousands of requests and exhaust your API quota.

**Fix:** Add `slowapi` rate limiting:
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/api/query")
@limiter.limit("10/minute")
async def process_legal_query(req: QueryRequest, request: Request):
    ...
```

---

#### 4. `/api/ocr-extract` and `/api/ocr-query` Have No Auth Guard
**File:** `backend/app/main.py` (lines 356–408)

OCR endpoints accept arbitrary file uploads with **no authentication required**. An attacker can:
- Upload malicious PDFs crafted to exploit PaddleOCR parsing
- Cause unbounded CPU consumption on your server (PaddleOCR is computationally expensive)
- Use your server as a free OCR service

**Fix:** Add `get_current_user` dependency to these endpoints:
```python
@app.post("/api/ocr-extract")
async def ocr_extract(image: UploadFile = File(...), lang: str = Form(default="hi"), 
                      user = Depends(get_current_user)):
```
Or at minimum add file size validation:
```python
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
file_bytes = await image.read()
if len(file_bytes) > MAX_FILE_SIZE:
    raise HTTPException(413, "File too large")
```

---

### 🟡 MEDIUM — Should Fix Before Production

#### 5. `allow_methods=["*"]` and `allow_headers=["*"]` in CORS
**File:** `backend/app/main.py` (line 97–98)

Wildcard methods/headers in production CORS are overly permissive. Should be locked down to only what the frontend actually uses.

**Fix:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)
```

---

#### 6. No File Type Validation on Upload Endpoints
**File:** `backend/app/main.py` (OCR and document ingest endpoints)

The backend accepts any file type with no MIME validation. Users could upload `.exe`, `.js`, or other files.

**Fix:**
```python
ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
if image.content_type not in ALLOWED_MIME:
    raise HTTPException(415, f"Unsupported file type: {image.content_type}")
```

---

#### 7. Internal Stack Traces Exposed in 500 Responses
**File:** `backend/app/main.py` (multiple `except Exception as e: raise HTTPException(500, detail=str(e))`)

`str(e)` from internal exceptions can expose file paths, library versions, DB table names, and internal logic to the client.

**Fix:** Use generic messages in production:
```python
except Exception as e:
    print(f"[ERROR] {e}")  # log server-side only
    raise HTTPException(status_code=500, detail="An internal error occurred")
```

---

#### 8. `lovable-tagger` DevDependency Leaks in Production Build
**File:** `frontend/vite.config.ts` (line 21)

`lovable-tagger` is a Lovable.dev platform tool that injects build metadata. It's conditionally disabled in non-development mode, but it's an unnecessary third-party dependency that could be removed entirely.

**Fix:** Remove from `package.json` and `vite.config.ts` before production.

---

#### 9. `pytest`, `ragas`, `datasets` are in Production `requirements.txt`
**File:** `backend/requirements.txt`

Testing and evaluation libraries (pytest, ragas, datasets) should not be installed on production servers. They add ~500MB+ of unnecessary packages and increase attack surface.

**Fix:** Split into `requirements.txt` (prod) and `requirements-dev.txt` (dev/test).

---

### 🟢 LOW — Good Practices to Add

#### 10. No `Content-Security-Policy` (CSP) Headers
The frontend has no CSP headers configured, meaning any injected scripts (XSS) run without restriction.

#### 11. Missing `Helmet`-equivalent Headers
No `X-Frame-Options`, `X-Content-Type-Options`, or `Strict-Transport-Security` headers.

---

## PART 2: Deployment Architecture & Hosting Plan

### Chosen Stack (All Free Tier)
| Component | Platform | Free Tier Limit |
|---|---|---|
| **Frontend** | **Vercel** | Unlimited static deploys, 100GB bandwidth |
| **Backend API** | **Render** | 750 hrs/month web service (free tier) |
| **Database** | **Neon (existing)** | 512MB pgvector storage free |
| **Auth** | **Supabase (existing)** | 500MB DB, 50k MAU free |
| **AI Inference** | **Groq API** | ~generous free tier |

> ⚠️ **Render Free Tier Caveat:** Services spin down after 15 minutes of inactivity and take ~30s to cold-start. For demo/portfolio this is acceptable. For production use, upgrade to the $7/month plan.

---

## PART 3: Step-by-Step Deployment Instructions

### STEP 1: Rotate All Secrets (DO THIS FIRST)

Before any public deploy, rotate every key that was in your `.env`:

1. **Groq API Key** → https://console.groq.com/keys → Delete old, create new
2. **Supabase Service Role Key** → Supabase Dashboard → Settings → API → Reset
3. **Supabase JWT Secret** → Supabase Dashboard → Settings → API → Rotate JWT secret
4. **Tavily/Serper** → Respective dashboards → regenerate

---

### STEP 2: Deploy Frontend to Vercel

1. **Install Vercel CLI** (optional, or use web UI):
   ```bash
   npm i -g vercel
   ```

2. **Create `frontend/vercel.json`** (already created in this repo at `frontend/vercel.json`):
   ```json
   {
     "rewrites": [{ "source": "/(.*)", "destination": "/index.html" }]
   }
   ```

3. **Go to** https://vercel.com → New Project → Import from GitHub → Select `LegalSarathi` repo

4. **Configure build settings:**
   - Framework: `Vite`
   - Root Directory: `frontend`
   - Build Command: `npm run build`
   - Output Directory: `dist`

5. **Add Environment Variables** in Vercel dashboard:
   ```
   VITE_SUPABASE_URL=https://fyeerrlvdubjmotshqnr.supabase.co
   VITE_SUPABASE_ANON_KEY=<your_new_rotated_key>
   VITE_API_BASE_URL=https://your-render-backend.onrender.com
   ```

6. Click **Deploy**. Vercel auto-deploys on every `git push` to `main`.

---

### STEP 3: Deploy Backend to Render

1. **Go to** https://render.com → New → Web Service

2. **Connect your GitHub repo** and select `LegalSarathi`

3. **Configure the service:**
   - Name: `legalsarathi-backend`
   - Root Directory: `backend`
   - Environment: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

4. **Add ALL Environment Variables** in the Render dashboard (from your `.env`):
   ```
   GROQ_API_KEY=<rotated>
   NEON_DATABASE_URL=<your_neon_url>
   SUPABASE_URL=https://fyeerrlvdubjmotshqnr.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=<rotated>
   SUPABASE_JWT_SECRET=<rotated>
   TAVILY_API_KEY=<rotated>
   SERPER_API_KEY=<rotated>
   TRANSLATION_BACKEND=fast
   SEARCH_PROVIDER_PRIORITY=tavily,serper
   FRONTEND_URL=https://your-vercel-app.vercel.app
   ```

5. Click **Create Web Service**. Render will build and deploy.

6. **Copy your Render URL** (e.g., `https://legalsarathi-backend.onrender.com`)

---

### STEP 4: Connect Frontend to Backend

1. Go back to Vercel → your project → Settings → Environment Variables
2. Set: `VITE_API_BASE_URL=https://legalsarathi-backend.onrender.com`
3. Redeploy the frontend (trigger from Vercel dashboard or `git push`)

---

### STEP 5: Update Supabase Auth URLs

1. Go to Supabase Dashboard → Authentication → URL Configuration
2. Add to **Allowed Redirect URLs:**
   ```
   https://your-vercel-app.vercel.app/**
   ```
3. Set **Site URL:**
   ```
   https://your-vercel-app.vercel.app
   ```

---

### STEP 6: Update Backend CORS

After you have your Vercel URL, update `backend/app/main.py`:
```python
_ALLOWED_ORIGINS = [
    "https://your-vercel-app.vercel.app",
    "http://localhost:8080",   # local dev only
    "http://localhost:5173",
]
```
And set the `FRONTEND_URL` env var on Render to your Vercel domain so it's picked up dynamically.

---

### STEP 7: Test the Full Deployment

1. Open your Vercel URL
2. Complete onboarding flow
3. Send a test chat message — should hit Render backend → Groq → response
4. Test Lawyers page with city filters
5. Test Document download

---

## PART 4: Free Tier Limitations to Know

| Limitation | Impact |
|---|---|
| Render free tier sleeps after 15min inactivity | First request after sleep takes ~30 seconds |
| PaddleOCR downloads ~80MB model on first use | Render may time out on cold start with OCR |
| 512MB RAM on Render free | `torch` + `sentence-transformers` may push limits; consider removing `torch` and using CPU-only sentence-transformers |
| Vercel 10s function timeout | Not applicable since Vercel only serves static files here |

### Recommended: Slim the Backend for Free Hosting

Remove heavy dev-only packages before deploying to Render. Comment out in `requirements.txt`:
```
# torch==2.5.1        -- 800MB, not needed if using Groq for inference
# paddlepaddle==2.6.2 -- 400MB, only needed for OCR feature
# ragas==0.1.21       -- testing only
# datasets==2.21.0    -- testing only
# pytest==8.3.3       -- testing only
```
This brings the install size from ~3GB down to ~300MB — well within free tier limits.
