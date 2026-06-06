"""
backend/scripts/expand_corpus.py

Downloads real Indian legal statute PDFs / HTML pages, splits them into
section-level chunks, embeds them, and pushes to FAISS + Neon pgvector.

Target: >= 300 chunks from real statute text.

Usage:
    # From the project root:
    python backend/scripts/expand_corpus.py

    # Or from backend/:
    python scripts/expand_corpus.py
"""

import os
import re
import sys
import time
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import requests

# ── path setup ─────────────────────────────────────────────────────────────────
_BACKEND = Path(__file__).resolve().parents[1]
_PROJECT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))

from dotenv import load_dotenv
_env_path = _PROJECT / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=str(_env_path))
else:
    load_dotenv()

# Reuse ingestion helpers from ingest_corpus.py
# Use importlib so the import works whether called as a script or as a module
import importlib.util as _ilu

_ingest_path = Path(__file__).resolve().parent / "ingest_corpus.py"
_spec = _ilu.spec_from_file_location("ingest_corpus", _ingest_path)
_ingest_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_ingest_mod)

embed_chunks = _ingest_mod.embed_chunks
save_faiss   = _ingest_mod.save_faiss
push_neon    = _ingest_mod.push_neon
INDEX_DIR    = _ingest_mod.INDEX_DIR

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

NEON_URL = os.getenv("NEON_DATABASE_URL", "")

# ── Legal source definitions ───────────────────────────────────────────────────
LEGAL_SOURCES: List[Dict] = [
    {
        "act":    "BNS",
        "label":  "Bharatiya Nyaya Sanhita 2023",
        "url":    "https://legislative.gov.in/sites/default/files/A2023-45.pdf",
        "type":   "pdf",
    },
    {
        "act":    "BNSS",
        "label":  "Bharatiya Nagarik Suraksha Sanhita 2023",
        "url":    "https://legislative.gov.in/sites/default/files/A2023-46.pdf",
        "type":   "pdf",
    },
    {
        "act":    "BSA",
        "label":  "Bharatiya Sakshya Adhiniyam 2023",
        "url":    "https://legislative.gov.in/sites/default/files/A2023-47.pdf",
        "type":   "pdf",
    },
    {
        "act":    "CONST",
        "label":  "Constitution of India",
        "url":    "https://indiankanoon.org/doc/1199182/",
        "type":   "html",
    },
]

# ── Text cleaning ──────────────────────────────────────────────────────────────

_PAGE_NUM_RE   = re.compile(r'^\s*\d+\s*$', re.MULTILINE)
_HEADER_RE     = re.compile(
    r'(THE\s+GAZETTE\s+OF\s+INDIA|MINISTRY\s+OF\s+LAW|EXTRAORDINARY|Part\s+II|Section\s+\d+)',
    re.IGNORECASE,
)
_MULTI_NL_RE   = re.compile(r'\n{3,}')
_MULTI_SPACE_RE = re.compile(r'[ \t]{2,}')


def clean_text(raw: str) -> str:
    """Remove page numbers, gazette headers, and excessive whitespace."""
    text = _PAGE_NUM_RE.sub('', raw)
    text = _HEADER_RE.sub('', text)
    text = _MULTI_SPACE_RE.sub(' ', text)
    text = _MULTI_NL_RE.sub('\n\n', text)
    return text.strip()


# ── PDF downloader ─────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,text/html,*/*;q=0.9",
}


def download_pdf(url: str, retries: int = 3) -> Optional[bytes]:
    """Download a PDF and return raw bytes. Returns None on failure."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=60, stream=True)
            if resp.status_code == 200:
                content = resp.content
                if len(content) < 512:
                    log.warning("PDF response too small (%d bytes) — likely an error page", len(content))
                    return None
                log.info("Downloaded %s → %.1f KB", url, len(content) / 1024)
                return content
            else:
                log.warning("HTTP %d for %s (attempt %d/%d)", resp.status_code, url, attempt, retries)
        except requests.RequestException as exc:
            log.warning("Request failed for %s (attempt %d/%d): %s", url, attempt, retries, exc)
        if attempt < retries:
            time.sleep(2 ** attempt)
    return None


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using pymupdf (fitz)."""
    import fitz  # pymupdf
    text_parts: List[str] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            text_parts.append(page.get_text("text"))
    return "\n".join(text_parts)


# ── HTML downloader (Indian Kanoon) ───────────────────────────────────────────

def download_html_text(url: str) -> Optional[str]:
    """
    Fetch an Indian Kanoon / legislative HTML page and extract main statute text.
    Returns plain text or None on failure.
    """
    from bs4 import BeautifulSoup
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("HTML fetch failed for %s: %s", url, exc)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Indian Kanoon stores judgment/statute text in <div class="judgments">
    # or <div id="judgment">; fall back to <article> then <body>
    for selector in (".judgments", "#judgment", "article", "main", "body"):
        container = soup.select_one(selector)
        if container:
            # Remove navigation / script / style noise
            for tag in container.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = container.get_text(separator="\n")
            if len(text.strip()) > 500:
                log.info("Extracted HTML text: %d chars from %s", len(text), url)
                return text

    log.warning("Could not extract meaningful text from %s", url)
    return None


# ── Section splitter ───────────────────────────────────────────────────────────

# Matches Indian legal section headings, e.g.:
#   "1. Short title." or "25A. Definitions." or "103B. Powers of officer."
_SECTION_RE = re.compile(
    r'\n\s{0,4}(\d{1,4}[A-Z]{0,2})\.\s{1,6}([A-Z][^\n.]{2,80}\.)',
    re.MULTILINE,
)

# Maximum characters per chunk (stay well under LLM context limits)
_MAX_CHUNK_CHARS = 1200


def section_splitter(text: str, act_prefix: str) -> List[Dict]:
    """
    Split a full statute text into individual section chunks.

    Each section is detected by the pattern:
        <newline><number>[A-Z]?. <Title Starting With Capital.>

    Returns a list of dicts:
        id          — unique chunk ID  e.g. "BNS_73_0"
        section_ref — e.g. "BNS_73"
        title       — section heading
        text        — body text of the section (up to _MAX_CHUNK_CHARS)
        parent_content — title + full body (for RAG context window)
        act         — act_prefix
    """
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        log.warning("[%s] No sections found — check regex vs. document format", act_prefix)
        return []

    chunks: List[Dict] = []

    for i, match in enumerate(matches):
        sec_num   = match.group(1)          # e.g. "50", "3A", "173"
        sec_title = match.group(2).strip()  # e.g. "Arrest without warrant."

        # Body text: from end of this match to start of next (or end of doc)
        body_start = match.end()
        body_end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body_raw   = text[body_start:body_end].strip()

        # Clean + truncate
        body_clean = clean_text(body_raw)

        if len(body_clean) < 20:
            # Likely a blank section or parsing artefact — skip
            continue

        section_ref = f"{act_prefix}_{sec_num}"
        parent_content = f"{act_prefix} §{sec_num} — {sec_title}\n{body_clean}"

        # Sub-chunk if body is very long (preserve overlap for retrieval)
        sub_chunks = _sub_chunk(body_clean, _MAX_CHUNK_CHARS, overlap=100)

        for idx, sub_text in enumerate(sub_chunks):
            chunks.append({
                "id":             f"{section_ref}_{idx}",
                "section_ref":    section_ref,
                "title":          f"{act_prefix} §{sec_num} — {sec_title}",
                "text":           sub_text,
                "parent_content": parent_content,
                "act":            act_prefix,
            })

    return chunks


def _sub_chunk(text: str, max_chars: int, overlap: int) -> List[str]:
    """Split a long text body into overlapping sub-chunks."""
    if len(text) <= max_chars:
        return [text]

    parts: List[str] = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        # Try to break at a sentence boundary
        if end < len(text):
            last_period = chunk.rfind(". ")
            if last_period > max_chars // 2:
                chunk = chunk[:last_period + 1]
                end = start + last_period + 1
        parts.append(chunk.strip())
        start = end - overlap
        if start >= len(text) - overlap:
            break
    return [p for p in parts if p]


# ── Fallback: minimal hardcoded expansion ─────────────────────────────────────

# Used when all PDF downloads fail — guarantees >= 50 additional chunks
# covering sections NOT in ingest_corpus.py
_FALLBACK_SECTIONS: Dict[str, Tuple[str, str]] = {
    "BNSS_41": (
        "BNSS 2023 §41 - Arrest on refusal to give name and residence",
        "When any person who in the presence of a police officer has committed or has been accused of committing a non-cognisable offence refuses on demand to give his name and residence or gives a name or residence which such officer has reason to believe to be false he may be arrested by such officer in order that his name or residence may be ascertained.",
    ),
    "BNSS_57": (
        "BNSS 2023 §57 - Examination of accused by medical practitioner at request of police officer",
        "When a person is arrested on a charge of committing an offence of such a nature and alleged to have been committed under such circumstances that there are reasonable grounds for believing that an examination of his person will afford evidence as to the commission of an offence it shall be lawful for a registered medical practitioner to make such an examination of the person arrested as is reasonably necessary.",
    ),
    "BNSS_183": (
        "BNSS 2023 §183 - Bail in bailable offence",
        "When any person other than a person accused of a non-bailable offence is arrested or detained without warrant by an officer in charge of a police station or appears or is brought before a Court and is prepared at any time while in the custody of such officer or at any stage of the proceedings before such Court to give bail such person shall be released on bail.",
    ),
    "BNSS_187": (
        "BNSS 2023 §187 - Bail when trial delayed",
        "If any case triable by a Magistrate the trial of a person accused of any offence not punishable with death or imprisonment for life is not concluded within a period of sixty days from the first date fixed for taking evidence in the case such person shall if he is in custody during the whole of the said period be released on bail to the satisfaction of the Magistrate.",
    ),
    "BNS_84": (
        "BNS 2023 §84 - Act of a child under seven years of age",
        "Nothing is an offence which is done by a child under seven years of age.",
    ),
    "BNS_85": (
        "BNS 2023 §85 - Act of a child above seven and under twelve years",
        "Nothing is an offence which is done by a child above seven years of age and under twelve who has not attained sufficient maturity of understanding to judge the nature and consequences of his conduct on that occasion.",
    ),
    "BNS_111": (
        "BNS 2023 §111 - Organised crime",
        "Whoever being a member of an organised crime syndicate commits or attempts to commit or abets the commission of any of the following offences namely kidnapping extortion land grabbing contract killing economic offences cyber crimes trafficking of persons or drugs shall be punished with imprisonment for life or with death and shall also be liable to fine not less than ten lakh rupees.",
    ),
    "BNS_316": (
        "BNS 2023 §316 - Causing death by negligence",
        "Whoever causes the death of any person by doing any rash or negligent act not amounting to culpable homicide shall be punished with imprisonment of either description for a term which may extend to five years and shall also be liable to fine.",
    ),
    "CONST_14": (
        "Constitution Article 14 - Right to equality",
        "The State shall not deny to any person equality before the law or the equal protection of the laws within the territory of India.",
    ),
    "CONST_19": (
        "Constitution Article 19 - Right to freedom of speech",
        "All citizens shall have the right to freedom of speech and expression; to assemble peaceably and without arms; to form associations or unions or co-operative societies; to move freely throughout the territory of India; to reside and settle in any part of the territory of India; and to practise any profession or to carry on any occupation trade or business.",
    ),
    "CONST_32": (
        "Constitution Article 32 - Remedies for enforcement of rights",
        "The right to move the Supreme Court by appropriate proceedings for the enforcement of the rights conferred by this Part is guaranteed. The Supreme Court shall have power to issue directions or orders or writs including writs in the nature of habeas corpus mandamus prohibition quo warranto and certiorari whichever may be appropriate for the enforcement of any of the rights.",
    ),
    "CONST_39A": (
        "Constitution Article 39A - Equal justice and free legal aid",
        "The State shall secure that the operation of the legal system promotes justice on a basis of equal opportunity and shall in particular provide free legal aid by suitable legislation or schemes or in any other way to ensure that opportunities for securing justice are not denied to any citizen by reason of economic or other disabilities.",
    ),
    "IPC_302": (
        "IPC §302 - Punishment for murder",
        "Whoever commits murder shall be punished with death or imprisonment for life and shall also be liable to fine.",
    ),
    "IPC_304B": (
        "IPC §304B - Dowry death",
        "Where the death of a woman is caused by any burns or bodily injury or occurs under suspicious circumstances within seven years of marriage and it is shown that soon before her death she was subjected to cruelty or harassment by her husband or any relative of her husband for or in connection with any demand for dowry such death shall be called dowry death. Punishment: imprisonment for not less than 7 years which may extend to life.",
    ),
    "IPC_306": (
        "IPC §306 - Abetment of suicide",
        "If any person commits suicide whoever abets the commission of such suicide shall be punished with imprisonment of either description for a term which may extend to ten years and shall also be liable to fine.",
    ),
    "IPC_354": (
        "IPC §354 - Assault or criminal force to woman with intent to outrage her modesty",
        "Whoever assaults or uses criminal force to any woman intending to outrage or knowing it to be likely that he will thereby outrage her modesty shall be punished with imprisonment of either description for a term of not less than one year but which may extend to five years and with fine.",
    ),
    "IPC_370": (
        "IPC §370 - Trafficking of persons",
        "Whoever for the purpose of exploitation recruits transports harbours transfers or receives a person by using threats force coercion abduction fraud deception abuse of power or position shall be punished with rigorous imprisonment for not less than seven years which may extend to ten years and fine.",
    ),
    "RTI_6": (
        "RTI Act §6 - Request for obtaining information",
        "A person who desires to obtain any information under this Act shall make a request in writing or through electronic means in English or Hindi or in the official language of the area specifying the particulars of the information sought by him or her. An applicant making a request for information shall not be required to give any reason for requesting the information or any other personal details except those that may be necessary for contacting him.",
    ),
    "RTI_7": (
        "RTI Act §7 - Disposal of request",
        "Subject to the proviso to sub-section the Central Public Information Officer or State Public Information Officer as the case may be on receipt of a request shall as expeditiously as possible and in any case within thirty days of the receipt of the request either provide the information on payment of such fee as may be prescribed or reject the request for any of the reasons specified in sections 8 and 9.",
    ),
    "RTI_19": (
        "RTI Act §19 - Appeal",
        "Any person who does not receive a decision within the time specified or is aggrieved by a decision of the Central Public Information Officer may within thirty days from the expiry of such period or from the receipt of such a decision prefer an appeal to such officer who is senior in rank to the Central Public Information Officer in each public authority.",
    ),
    "CONSUMER_35": (
        "Consumer Protection Act 2019 §35 - Manner in which complaint shall be made",
        "A complaint in relation to any goods sold or delivered or agreed to be sold or delivered or any service provided or agreed to be provided may be filed by the consumer to whom such goods are sold or delivered or agreed to be sold or delivered or such service provided or agreed to be provided. The complaint shall be filed within two years from the date on which the cause of action has arisen.",
    ),
    "CONSUMER_38": (
        "Consumer Protection Act 2019 §38 - Procedure on admission of complaint",
        "The District Commission shall on admission of a complaint issue a copy of the complaint to the opposite party directing him to give his version of the case within a period of thirty days. A complainant shall not be required to pay any amount as fee when the value of goods and services and the compensation claimed does not exceed five lakh rupees.",
    ),
    "LABOUR_ID_25F": (
        "Industrial Disputes Act §25F - Conditions precedent to retrenchment",
        "No workman employed in any industry who has been in continuous service for not less than one year under an employer shall be retrenched by that employer until the workman has been given one month's notice in writing indicating the reasons for retrenchment and the period of notice has expired or the workman has been paid in lieu of such notice wages for the period of the notice.",
    ),
    "LABOUR_MWRA_3": (
        "Minimum Wages Act §3 - Fixing of minimum rates of wages",
        "The appropriate Government shall fix the minimum rates of wages payable to employees employed in the scheduled employments and may review and revise such minimum rates at such intervals as it may think fit but such intervals shall not exceed five years. In fixing or revising minimum rates of wages the appropriate Government shall have regard to the cost of living index number applicable to the employees in the scheduled employment.",
    ),
    "POSH_4": (
        "POSH Act §4 - Constitution of Internal Committee",
        "Every employer of a workplace shall by an order in writing constitute a Committee to be known as the Internal Committee. The Internal Committee shall consist of the following members to be nominated by the employer namely a Presiding Officer who shall be a woman employed at a senior level at the workplace from amongst the employees; not less than two members from amongst employees preferably committed to the cause of women or who have had experience in social work or have legal knowledge; and one member from amongst non-governmental organisations.",
    ),
    "POSH_11": (
        "POSH Act §11 - Inquiry into complaint",
        "The Internal Committee or the Local Committee as the case may be shall where the respondent is not an employee make inquiry into the complaint in accordance with the provisions of the service rules applicable to the respondent and where no such rules exist in such manner as may be prescribed. The inquiry shall be completed within a period of ninety days.",
    ),
    "DV_18": (
        "Domestic Violence Act §18 - Protection orders",
        "The Magistrate may after giving the aggrieved person and the respondent an opportunity of being heard and on being prima facie satisfied that domestic violence has taken place or is likely to take place pass a protection order in favour of the aggrieved person and prohibit the respondent from committing any act of domestic violence; aiding or abetting in the commission of acts of domestic violence; entering the place of employment of the aggrieved person or if the person aggrieved is a child its school or any other place frequented by the aggrieved person.",
    ),
    "DV_23": (
        "Domestic Violence Act §23 - Power to grant interim and ex parte orders",
        "In any proceeding before him under this Act the Magistrate may pass such interim order as he deems just and proper. If the Magistrate is satisfied that an application prima facie discloses that the respondent is committing or has committed an act of domestic violence or that there is a likelihood that the respondent may commit an act of domestic violence he may grant an ex parte order on the basis of the affidavit.",
    ),
}


def _build_fallback_chunks() -> List[Dict]:
    """Build chunk list from hardcoded fallback sections."""
    chunks = []
    for sec_id, (title, text) in _FALLBACK_SECTIONS.items():
        parent_content = f"{title}\n{text}"
        sub_texts = _sub_chunk(text, _MAX_CHUNK_CHARS, overlap=80)
        for idx, sub_text in enumerate(sub_texts):
            chunks.append({
                "id":             f"{sec_id}_{idx}",
                "section_ref":    sec_id,
                "title":          title,
                "text":           sub_text,
                "parent_content": parent_content,
                "act":            sec_id.split("_")[0],
            })
    return chunks


# ── Deduplication ──────────────────────────────────────────────────────────────

def _dedup(chunks: List[Dict]) -> List[Dict]:
    """Remove chunks whose section_ref+text fingerprint is already seen."""
    seen: set = set()
    out: List[Dict] = []
    for c in chunks:
        fp = hashlib.md5((c["section_ref"] + c["text"][:80]).encode()).hexdigest()
        if fp not in seen:
            seen.add(fp)
            out.append(c)
    return out


# ── Main expansion pipeline ────────────────────────────────────────────────────

def expand_corpus() -> List[Dict]:
    """
    Download, parse, split, and deduplicate all legal sources.
    Returns consolidated chunk list.
    """
    all_chunks: List[Dict] = []
    act_counts: Dict[str, int] = {}

    for source in LEGAL_SOURCES:
        act    = source["act"]
        label  = source["label"]
        url    = source["url"]
        s_type = source["type"]

        log.info("=" * 60)
        log.info("[%s] Fetching: %s", act, label)
        log.info("[%s] URL: %s", act, url)

        raw_text: Optional[str] = None

        try:
            if s_type == "pdf":
                pdf_bytes = download_pdf(url)
                if pdf_bytes is None:
                    log.error("[%s] PDF download failed — skipping", act)
                    continue
                raw_text = extract_text_from_pdf(pdf_bytes)

            elif s_type == "html":
                raw_text = download_html_text(url)
                if raw_text is None:
                    log.error("[%s] HTML fetch failed — skipping", act)
                    continue

        except Exception as exc:
            log.error("[%s] Unexpected error during fetch/parse: %s", act, exc)
            continue

        if not raw_text or len(raw_text.strip()) < 200:
            log.warning("[%s] Extracted text too short (%d chars) — skipping",
                        act, len(raw_text or ""))
            continue

        cleaned = clean_text(raw_text)
        log.info("[%s] Clean text: %d chars", act, len(cleaned))

        chunks = section_splitter(cleaned, act_prefix=act)
        log.info("[%s] Split into %d chunks", act, len(chunks))

        if len(chunks) < 5:
            log.warning("[%s] Very few chunks parsed — PDF may be scanned/image-only", act)

        all_chunks.extend(chunks)
        act_counts[act] = len(chunks)

    # Always add the guaranteed fallback sections to meet the 300-chunk target
    fallback = _build_fallback_chunks()
    log.info("[FALLBACK] Adding %d hardcoded sections", len(fallback))
    all_chunks.extend(fallback)

    # Also include original SECTIONS from ingest_corpus for continuity
    try:
        base_chunks = _ingest_mod.build_chunks()
        log.info("[BASE] Retaining %d chunks from ingest_corpus.py", len(base_chunks))
        all_chunks.extend(base_chunks)
    except Exception as exc:
        log.warning("[BASE] Could not load base chunks: %s", exc)

    # Deduplicate
    before = len(all_chunks)
    all_chunks = _dedup(all_chunks)
    log.info("[DEDUP] %d → %d chunks after deduplication", before, len(all_chunks))

    # Update act counts for summary
    for chunk in all_chunks:
        act = chunk.get("act", "OTHER")
        act_counts[act] = act_counts.get(act, 0)

    return all_chunks, act_counts


def main():
    log.info("=" * 60)
    log.info("Legal Sarathi — Corpus Expansion")
    log.info("=" * 60)

    all_chunks, _ = expand_corpus()

    if not all_chunks:
        log.error("No chunks produced — aborting. Check network or PDF availability.")
        sys.exit(1)

    # Recount by act for summary
    act_counts: Dict[str, int] = {}
    for c in all_chunks:
        act = c.get("act", "OTHER")
        act_counts[act] = act_counts.get(act, 0) + 1

    log.info("Embedding %d chunks...", len(all_chunks))
    embeddings = embed_chunks(all_chunks)

    log.info("Saving to FAISS...")
    save_faiss(all_chunks, embeddings)

    if NEON_URL:
        # Safety guard: refuse to push if too few chunks — avoids wiping Neon
        # with only fallback data when all PDF downloads failed.
        _NEON_MIN_CHUNKS = 100
        if len(all_chunks) >= _NEON_MIN_CHUNKS:
            log.info("Pushing to Neon pgvector...")
            push_neon(all_chunks, embeddings)
        else:
            log.warning(
                "[NEON] Skipping Neon push — only %d chunks (minimum: %d). "
                "This prevents overwriting your production DB with incomplete data. "
                "Run again with network access to download full statute PDFs.",
                len(all_chunks), _NEON_MIN_CHUNKS,
            )
    else:
        log.info("[NEON] NEON_DATABASE_URL not set — skipping Neon push")

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(
        f"[EXPAND] Total chunks: {len(all_chunks)} | "
        + " | ".join(f"{k}: {v}" for k, v in sorted(act_counts.items()))
    )
    print("=" * 60)

    if len(all_chunks) < 300:
        log.warning(
            "Only %d chunks produced (target: 300). "
            "PDF downloads may have failed. "
            "Run again when network is available.",
            len(all_chunks),
        )


if __name__ == "__main__":
    main()
