# LegalSarathi QA Test Report

Date: 2026-05-02
Role: Senior software tester

## Startup Test

Backend command requested:

```powershell
cd backend
.\venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

Result: Pass after running outside the sandbox. Backend health returned `200 OK` from `http://127.0.0.1:8000/api/health`.

Frontend command requested:

```powershell
cd frontend
npm run dev
```

Result: Pass after using `npm.cmd` instead of `npm.ps1` and running outside the sandbox. Vite served the app at `http://127.0.0.1:5174/` because `5173` was already occupied.

## Automated Component/Route Smoke Test

Created test case:

- `frontend/src/test/app-route-smoke.test.tsx`

Coverage:

- Public routes: `/`, `/splash`, `/onboarding`, `/onboarding-form`, `/language`, `/login`, `/otp`, `/about`, `/privacy`, `/terms`, unknown route.
- Protected routes with seeded guest session: `/home`, `/chat`, `/documents`, `/documents/new`, `/portal-tracker`, `/lawyers`, `/lawyers/:id`, `/rti`, `/community`, `/community/new`, `/community/:id`, `/notifications`, `/cases`, `/profile`, `/profile/edit`, `/profile/saved-documents`, `/profile/appointments`, `/profile/saved-lawyers`, `/profile/help`.
- Assertion: route renders without hitting the app error boundary message, `Something went wrong`.

Result:

```text
Test Files  3 passed (3)
Tests       32 passed (32)
```

## Build/Test Results

Frontend build:

```text
npm.cmd run build
Result: Pass
Warning: bundle chunk larger than 500 kB
Warning: Browserslist/caniuse-lite data is 11 months old
```

Frontend tests:

```text
npm.cmd test
Result: Pass, 32 tests passed
```

Frontend lint:

```text
npm.cmd run lint
Result: Fail, 19 errors and 32 warnings
```

Backend tests:

```text
.\venv\Scripts\python.exe -m pytest -q
Result: Fail, pytest is not installed in the backend venv
```

## API Checks

`GET /api/health`: Pass, returned healthy status.

`POST /api/query`: Pass, returned legal guidance payload with summary, rights, action steps, citations, and help channels.

`POST /api/tts`: Pass, returned MP3 bytes in response.

`POST /api/ocr-extract`: Pass, extracted text from `test_download.pdf`.

`POST /api/download-pdf`: Fail, returned `500` with empty detail.

`POST /api/generate-draft-pdf`: Fail, returned `500` with empty detail.

## Defect List

1. PDF endpoints return `500` with empty error detail.
   - Affected endpoints: `/api/download-pdf`, `/api/generate-draft-pdf`.
   - Likely area: `backend/app/services/pdf_service.py`, Playwright launch/PDF generation block.
   - Impact: document download flow fails from the frontend.

2. Frontend lint fails on React hooks rule.
   - File: `frontend/src/pages/OnboardingForm.tsx`
   - Issue: `navigate("/login")` and `return null` happen before `useMemo`, causing conditional hook order.
   - Runtime warning observed: `You should call navigate() in a React.useEffect(), not when your component is first rendered.`

3. Frontend lint fails on empty interface declarations.
   - `frontend/src/components/ui/command.tsx`
   - `frontend/src/components/ui/textarea.tsx`
   - Desktop duplicates also fail under `frontend/src/desktop_ui/components/ui/`.

4. Frontend lint fails on explicit `any` and unused expression issues.
   - Main file: `frontend/src/pages/Chat.tsx`
   - Impact: type safety and CI lint gate failure.

5. Backend test runner is unavailable.
   - `pytest` is missing from `backend/venv`.
   - Impact: backend regression tests cannot run in the current environment.

6. Dev startup has Windows/tooling friction.
   - `npm run dev` through PowerShell hits `npm.ps1` execution policy.
   - Vite/Vitest need outside-sandbox execution because esbuild spawn fails with `EPERM`.
   - `uvicorn --reload` needs outside-sandbox execution because Windows named pipe creation fails.

7. Browser automation with Playwright CLI did not complete.
   - `playwright-cli open` returned, but `snapshot`, `list`, and `close-all` hung until timeout.
   - Impact: full click-through browser automation could not be completed with this CLI session.
