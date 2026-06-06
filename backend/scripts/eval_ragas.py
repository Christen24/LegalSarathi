"""
backend/scripts/eval_ragas.py

Ragas evaluation pipeline for Legal Sarathi RAG system.

Runs two retrieval configurations on 25 real citizen questions:
  - Baseline:  dense-only retrieval (RAGService.retrieve())
  - Enhanced:  hybrid BM25+dense + CrossEncoder reranking

Produces a comparison table and saves results to backend/data/eval_results.json.

Usage:
    # From project root:
    python backend/scripts/eval_ragas.py

    # From backend/:
    python scripts/eval_ragas.py

Requires:
    - GROQ_API_KEY in environment (or .env)
    - FAISS index built (run ingest_corpus.py or expand_corpus.py first)
    - pip install ragas==0.1.21 datasets
"""

import os
import sys
import json
import time
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Tuple, Any

# ── path setup ─────────────────────────────────────────────────────────────────
_BACKEND = Path(__file__).resolve().parents[1]
_PROJECT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))

from dotenv import load_dotenv
_env = _PROJECT / ".env"
if _env.exists():
    load_dotenv(dotenv_path=str(_env))
else:
    load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Evaluation dataset ─────────────────────────────────────────────────────────
# 25 realistic citizen queries covering 6 domains.
# ground_truth answers are grounded in actual BNS/BNSS/IPC provisions.
EVAL_DATASET: List[Dict[str, str]] = [

    # ── Arrest rights (5) ──────────────────────────────────────────────────────
    {
        "question": "Can police arrest me without showing a warrant?",
        "ground_truth": (
            "Under BNSS Section 35, police can arrest without a warrant for cognizable offences. "
            "However, under BNSS Section 50, they must immediately inform you of the grounds of "
            "arrest. Under Constitution Article 22, you must be produced before a magistrate "
            "within 24 hours of arrest, excluding travel time."
        ),
    },
    {
        "question": "What should police do when arresting someone?",
        "ground_truth": (
            "When making an arrest under BNS Section 73, police must actually touch or confine "
            "the person. Under BNSS Section 50, they must immediately communicate the full "
            "particulars of the offence. Under BNSS Section 76, the arrested person must be "
            "taken before a magistrate without unnecessary delay."
        ),
    },
    {
        "question": "How long can police keep me in custody without taking me to a magistrate?",
        "ground_truth": (
            "Under BNSS Section 51 and Constitution Article 22, a person arrested without a "
            "warrant cannot be held in custody for more than 24 hours, excluding the journey "
            "time from the place of arrest to the magistrate's court. Detention beyond this "
            "period without a magistrate order is illegal."
        ),
    },
    {
        "question": "Do I have the right to know why I am being arrested?",
        "ground_truth": (
            "Yes. Under BNSS Section 50 and Constitution Article 22, you have an absolute right "
            "to be informed of the grounds of your arrest. Police must tell you immediately. "
            "Failure to inform you of arrest grounds is a violation of your fundamental rights."
        ),
    },
    {
        "question": "Can I be arrested for a non-cognizable offence without a magistrate order?",
        "ground_truth": (
            "No. For non-cognizable offences, police cannot arrest without an order from a "
            "Magistrate. Under BNSS Section 35, warrantless arrest is only permitted for "
            "cognizable offences. If arrested for a non-cognizable offence without a magistrate "
            "order, you can challenge the arrest as illegal."
        ),
    },

    # ── Bail (4) ───────────────────────────────────────────────────────────────
    {
        "question": "What is anticipatory bail and how do I apply for it?",
        "ground_truth": (
            "Anticipatory bail under BNSS Section 482 allows a person who has reason to believe "
            "they may be arrested for a non-bailable offence to apply to the High Court or "
            "Sessions Court for pre-arrest bail. If granted, they will be released on bail "
            "immediately upon arrest. Apply through a lawyer with an application to the "
            "Sessions Court or High Court."
        ),
    },
    {
        "question": "What is default bail or statutory bail?",
        "ground_truth": (
            "Default bail under BNSS Section 479 is your right to bail if the police fail to "
            "file a chargesheet within the statutory period — 60 days for magistrate-triable "
            "offences and 90 days for sessions-triable offences. You can apply for default bail "
            "before the magistrate and it cannot be defeated by a subsequent chargesheet filing."
        ),
    },
    {
        "question": "Am I entitled to bail for a bailable offence?",
        "ground_truth": (
            "Yes. Under BNSS Section 479 and 183, bail is a right for bailable offences. "
            "If you are arrested for a bailable offence, the police officer or magistrate "
            "must release you on bail as soon as you offer to furnish it. You cannot be "
            "refused bail for a bailable offence."
        ),
    },
    {
        "question": "Can police deny bail for a bailable offence?",
        "ground_truth": (
            "No. For bailable offences, bail is a matter of right under BNSS Section 183. "
            "Police or the court must release you on bail once you offer to furnish it. "
            "Denial of bail for a bailable offence is unlawful and you can approach the "
            "magistrate or High Court immediately."
        ),
    },

    # ── Domestic violence (4) ──────────────────────────────────────────────────
    {
        "question": "My husband is physically abusing me. What legal protection can I get?",
        "ground_truth": (
            "Under the Protection of Women from Domestic Violence Act 2005, you can obtain a "
            "Protection Order from a Magistrate (DV Act Section 18) prohibiting your husband "
            "from committing any act of domestic violence. You can also get a Residence Order "
            "to stay in the shared household, monetary relief, and custody of children. "
            "Contact the Protection Officer in your district or call Women Helpline 1091."
        ),
    },
    {
        "question": "Can I get a temporary protection order against my abusive partner quickly?",
        "ground_truth": (
            "Yes. Under DV Act Section 23, a Magistrate can pass an interim or ex parte "
            "protection order on the same day if satisfied that the respondent is committing "
            "or is likely to commit an act of domestic violence. File an application with the "
            "Protection Officer or directly before the Magistrate. Women helpline: 1091."
        ),
    },
    {
        "question": "What constitutes domestic violence under Indian law?",
        "ground_truth": (
            "The Protection of Women from Domestic Violence Act 2005 defines domestic violence "
            "as physical abuse, sexual abuse, verbal and emotional abuse, and economic abuse "
            "by a husband, live-in partner, or any member of the shared household. Economic "
            "abuse includes withholding money, assets, or preventing you from working. "
            "Even verbal threats and emotional harassment are covered."
        ),
    },
    {
        "question": "I am being harassed for dowry. What are my legal options?",
        "ground_truth": (
            "Dowry harassment is a criminal offence under IPC Section 498A (cruelty by husband "
            "or relatives), punishable with up to 3 years imprisonment. Dowry demands also "
            "violate the Dowry Prohibition Act 1961. You can file an FIR at any police station "
            "or approach a magistrate directly. You can also file under the Domestic Violence "
            "Act for protection and monetary relief. Call Women Helpline 181 or NCW 7827170170."
        ),
    },

    # ── RTI (4) ────────────────────────────────────────────────────────────────
    {
        "question": "How do I file an RTI application and how long does the government have to respond?",
        "ground_truth": (
            "Under RTI Act Section 6, file a written application to the Public Information "
            "Officer of the concerned department, specifying the information you seek. You can "
            "file online at rtionline.gov.in. The fee is Rs 10 for central government. Under "
            "RTI Act Section 7, the PIO must respond within 30 days. Response is free for BPL "
            "card holders. You need not give reasons for seeking information."
        ),
    },
    {
        "question": "What can I do if my RTI application is rejected or not responded to?",
        "ground_truth": (
            "Under RTI Act Section 19, if you receive no response within 30 days or are "
            "aggrieved by the decision, file a First Appeal within 30 days to the officer "
            "senior to the PIO in the same department. If still unsatisfied, file a Second "
            "Appeal to the Central or State Information Commission within 90 days of the first "
            "appeal decision. The Commission can impose penalties on defaulting officers."
        ),
    },
    {
        "question": "Can a government office refuse to give me information under RTI?",
        "ground_truth": (
            "Under RTI Act Sections 8 and 9, certain exemptions apply — information relating "
            "to national security, cabinet papers, personal information, and third-party "
            "commercial secrets may be withheld. However, information about corruption and "
            "human rights violations cannot be withheld. If refused, you can appeal to the "
            "First Appellate Authority and then to the Information Commission."
        ),
    },
    {
        "question": "Is there any fee to file an RTI and are any people exempt from paying it?",
        "ground_truth": (
            "The RTI application fee for central government departments is Rs 10. Below Poverty "
            "Line (BPL) card holders are completely exempt from all fees under the RTI Act. "
            "If you are charged more than Rs 10 or your BPL fee exemption is denied, you can "
            "raise this in your First Appeal to the senior officer in the same department."
        ),
    },

    # ── Consumer rights (4) ────────────────────────────────────────────────────
    {
        "question": "I was sold a defective product. How do I file a consumer complaint?",
        "ground_truth": (
            "Under Consumer Protection Act 2019 Section 35, file a complaint at the District "
            "Consumer Disputes Redressal Commission. For claims up to Rs 1 crore the District "
            "Commission has jurisdiction. No court fee is required for claims up to Rs 5 lakh. "
            "You must file within 2 years of the cause of action. You can also file online at "
            "consumerhelpline.gov.in or call the National Consumer Helpline 1800-11-4000."
        ),
    },
    {
        "question": "An e-commerce company refused to refund me for a defective item. What are my rights?",
        "ground_truth": (
            "Under Consumer Protection Act 2019, you have the right to get a refund or "
            "replacement for defective goods. E-commerce platforms are now covered under the "
            "Act. File a complaint at District Consumer Commission or at the National Consumer "
            "Helpline 1800-11-4000. You can seek compensation for deficiency in service. The "
            "time limit is 2 years from the date of refusal."
        ),
    },
    {
        "question": "How much does it cost to file a consumer forum complaint?",
        "ground_truth": (
            "Under Consumer Protection Act 2019 Section 38, there is no court fee for consumer "
            "complaints where the value of goods and services and compensation claimed does not "
            "exceed Rs 5 lakh. For claims above Rs 5 lakh, a prescribed fee applies. The "
            "opposite party must respond within 30 days of receiving a copy of the complaint."
        ),
    },
    {
        "question": "A private hospital charged me for services not rendered. Is this a consumer case?",
        "ground_truth": (
            "Yes. Medical services are covered under the Consumer Protection Act 2019 as "
            "services. A private hospital charging for services not rendered constitutes "
            "deficiency in service. File a complaint at the District Consumer Commission with "
            "all bills, receipts, and documentation. Seek refund and compensation. Call "
            "Consumer Helpline 1800-11-4000 for guidance before filing."
        ),
    },

    # ── Labour rights (4) ──────────────────────────────────────────────────────
    {
        "question": "My employer is paying me less than minimum wage. What can I do?",
        "ground_truth": (
            "Under the Minimum Wages Act 1948, your employer must pay you the minimum rate "
            "fixed by the State or Central Government. If wages are below minimum, file a "
            "complaint with the Labour Inspector of your area. The employer can be punished "
            "with imprisonment up to 6 months or fine up to Rs 500. You can also approach "
            "the Labour Commissioner or NALSA at 15100 for free legal assistance."
        ),
    },
    {
        "question": "My wages have not been paid for two months. What is my legal remedy?",
        "ground_truth": (
            "Under the Payment of Wages Act 1936, wages must be paid within 7 days after the "
            "wage period for establishments with fewer than 1000 workers. File a complaint "
            "with the Authority under the Payment of Wages Act (usually the Labour Commissioner). "
            "You can also file a complaint with the Labour Inspector. For free legal help "
            "contact NALSA helpline 15100."
        ),
    },
    {
        "question": "Can my employer fire me without any notice or compensation?",
        "ground_truth": (
            "Under Industrial Disputes Act Section 25F, a workman in continuous service for "
            "one year or more cannot be retrenched without one month's written notice stating "
            "reasons (or wages in lieu of notice) and payment of retrenchment compensation "
            "at the rate of 15 days' wages for every completed year of service. Retrenchment "
            "without following this procedure is illegal and can be challenged before the "
            "Labour Court."
        ),
    },
    {
        "question": "I was sexually harassed at my workplace. What are my rights under Indian law?",
        "ground_truth": (
            "Under the Sexual Harassment of Women at Workplace (Prevention, Prohibition and "
            "Redressal) Act 2013 (POSH), every employer must constitute an Internal Committee "
            "(POSH Section 4). File a written complaint to the Internal Committee within 3 "
            "months of the incident. The inquiry must be completed within 90 days (POSH "
            "Section 11). If no Internal Committee exists, file with the Local Committee. "
            "You can also file an FIR under BNS Section 75 (sexual harassment)."
        ),
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Service wrappers
# ═══════════════════════════════════════════════════════════════════════════════

def _init_services():
    """
    Initialise RAG, Reranker, and Groq services.
    Returns (rag_svc, reranker_svc, groq_svc) or raises on critical failure.
    """
    from app.services.rag_service import RAGService
    from app.services.reranker_service import RerankerService
    from app.services.groq_service import GroqService

    rag     = RAGService()
    reranker = RerankerService()
    groq    = GroqService()

    if not rag.is_ready:
        log.warning(
            "RAGService not ready — index not built. "
            "Run ingest_corpus.py / expand_corpus.py first. "
            "Evaluation will produce empty contexts."
        )

    return rag, reranker, groq


async def _get_answer_baseline(
    question: str,
    rag,
    groq,
    lang: str = "en",
) -> Tuple[str, List[str]]:
    """
    Baseline: dense-only retrieval → Groq synthesis.
    Returns (answer_text, list_of_context_strings).
    """
    chunks, _ = rag.retrieve(question, top_k=5)
    context_strings = [
        c.get("text") or c.get("content", "") for c in chunks
    ]
    rag_context = rag.format_for_prompt(chunks)

    buddy_data = await groq.synthesize_buddy_response(
        english_text=question,
        legal_keys=[],
        web_context="",
        target_lang=lang,
        specialist_opinion="",
        rag_context=rag_context,
        conversation_history=[],
        doc_context="",
    )
    answer = (
        buddy_data.get("situation_summary", "")
        + " "
        + " ".join(buddy_data.get("rights", []))
        + " "
        + " ".join(buddy_data.get("action_steps", []))
    ).strip()
    return answer, context_strings


async def _get_answer_enhanced(
    question: str,
    rag,
    reranker,
    groq,
    lang: str = "en",
) -> Tuple[str, List[str]]:
    """
    Enhanced: hybrid BM25+dense retrieval → CrossEncoder reranking → Groq synthesis.
    Returns (answer_text, list_of_context_strings).
    """
    chunks_raw, _ = rag.retrieve_hybrid(question, top_k=10)
    chunks = reranker.rerank(question, chunks_raw, top_k=5)
    context_strings = [
        c.get("text") or c.get("content", "") for c in chunks
    ]
    rag_context = rag.format_for_prompt(chunks)

    buddy_data = await groq.synthesize_buddy_response(
        english_text=question,
        legal_keys=[],
        web_context="",
        target_lang=lang,
        specialist_opinion="",
        rag_context=rag_context,
        conversation_history=[],
        doc_context="",
    )
    answer = (
        buddy_data.get("situation_summary", "")
        + " "
        + " ".join(buddy_data.get("rights", []))
        + " "
        + " ".join(buddy_data.get("action_steps", []))
    ).strip()
    return answer, context_strings


# ═══════════════════════════════════════════════════════════════════════════════
# Ragas evaluation
# ═══════════════════════════════════════════════════════════════════════════════

def _run_ragas(data_rows: List[Dict]) -> Dict[str, float]:
    """
    Build a Ragas EvaluationDataset and run faithfulness, answer_relevancy,
    context_precision.

    Returns dict: {metric_name: score}.
    Compatible with ragas==0.1.21.
    """
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy, context_precision

        hf_dataset = Dataset.from_list(data_rows)
        result = evaluate(
            dataset=hf_dataset,
            metrics=[faithfulness, answer_relevancy, context_precision],
        )
        # ragas.evaluate() returns an EvaluationResult; access scores as dict
        return {
            "faithfulness":      float(result["faithfulness"]),
            "answer_relevancy":  float(result["answer_relevancy"]),
            "context_precision": float(result["context_precision"]),
        }
    except ImportError as exc:
        log.error("ragas or datasets not installed: %s", exc)
        return {"faithfulness": -1.0, "answer_relevancy": -1.0, "context_precision": -1.0}
    except Exception as exc:
        log.error("Ragas evaluation error: %s", exc)
        return {"faithfulness": -1.0, "answer_relevancy": -1.0, "context_precision": -1.0}


# ═══════════════════════════════════════════════════════════════════════════════
# Main evaluation loop
# ═══════════════════════════════════════════════════════════════════════════════

async def _collect_data(rag, reranker, groq) -> Tuple[List[Dict], List[Dict]]:
    """
    Run all 25 questions through both configurations.
    Returns (baseline_rows, enhanced_rows) — each a list of ragas-compatible dicts.
    """
    baseline_rows: List[Dict] = []
    enhanced_rows: List[Dict] = []

    total = len(EVAL_DATASET)
    for i, item in enumerate(EVAL_DATASET):
        question     = item["question"]
        ground_truth = item["ground_truth"]

        log.info("[%d/%d] Question: %s", i + 1, total, question[:60])

        # ── Baseline ──────────────────────────────────────────────────────────
        try:
            b_answer, b_contexts = await _get_answer_baseline(question, rag, groq)
        except Exception as exc:
            log.warning("[BASELINE] Failed for Q%d: %s", i + 1, exc)
            b_answer   = ""
            b_contexts = []

        baseline_rows.append({
            "question":     question,
            "answer":       b_answer,
            "contexts":     b_contexts if b_contexts else ["No context retrieved."],
            "ground_truth": ground_truth,
        })

        # Rate-limit pause between baseline and enhanced calls
        await asyncio.sleep(1)

        # ── Enhanced ──────────────────────────────────────────────────────────
        try:
            e_answer, e_contexts = await _get_answer_enhanced(question, rag, reranker, groq)
        except Exception as exc:
            log.warning("[ENHANCED] Failed for Q%d: %s", i + 1, exc)
            e_answer   = ""
            e_contexts = []

        enhanced_rows.append({
            "question":     question,
            "answer":       e_answer,
            "contexts":     e_contexts if e_contexts else ["No context retrieved."],
            "ground_truth": ground_truth,
        })

        # Respect Groq rate limits (free tier: ~30 RPM for 70B)
        await asyncio.sleep(2)
        log.info("[%d/%d] Done. Baseline contexts: %d | Enhanced contexts: %d",
                 i + 1, total, len(b_contexts), len(e_contexts))

    return baseline_rows, enhanced_rows


def _print_table(baseline_scores: Dict, enhanced_scores: Dict):
    """Print a formatted comparison table to stdout."""
    metrics = ["faithfulness", "answer_relevancy", "context_precision"]

    print("\n" + "=" * 65)
    print("=== RAGAS EVALUATION RESULTS ===")
    print(f"{'Metric':<22} {'Baseline':>10} {'Enhanced':>10} {'Delta':>10}")
    print("-" * 65)

    for m in metrics:
        b = baseline_scores.get(m, -1.0)
        e = enhanced_scores.get(m, -1.0)
        if b >= 0 and e >= 0:
            delta = e - b
            delta_str = f"+{delta:.3f}" if delta >= 0 else f"{delta:.3f}"
            print(f"{m:<22} {b:>10.3f} {e:>10.3f} {delta_str:>10}")
        else:
            print(f"{m:<22} {'ERROR':>10} {'ERROR':>10} {'N/A':>10}")

    print("=" * 65)

    # Summary statement
    f_b = baseline_scores.get("faithfulness", -1)
    f_e = enhanced_scores.get("faithfulness", -1)
    if f_b >= 0 and f_e >= 0 and f_b > 0:
        improvement = ((f_e - f_b) / f_b) * 100
        direction   = "improved" if improvement >= 0 else "decreased"
        print(f"Reranking {direction} faithfulness by {abs(improvement):.1f}%")
    print()


def main():
    log.info("=" * 60)
    log.info("Legal Sarathi — Ragas Evaluation Pipeline")
    log.info("Questions: %d | Configs: 2 (baseline, enhanced)", len(EVAL_DATASET))
    log.info("=" * 60)

    # Init services
    rag, reranker, groq = _init_services()

    # Collect answers for both configs
    log.info("Collecting answers (this takes ~%d minutes at Groq free-tier rate)...",
             len(EVAL_DATASET) * 3 // 60 + 1)
    baseline_rows, enhanced_rows = asyncio.run(_collect_data(rag, reranker, groq))

    # Run Ragas
    log.info("Running Ragas evaluation on baseline config...")
    baseline_scores = _run_ragas(baseline_rows)
    log.info("Baseline scores: %s", baseline_scores)

    log.info("Running Ragas evaluation on enhanced config...")
    enhanced_scores = _run_ragas(enhanced_rows)
    log.info("Enhanced scores: %s", enhanced_scores)

    # Print table
    _print_table(baseline_scores, enhanced_scores)

    # Save results
    output_dir  = _BACKEND / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "eval_results.json"

    timestamp = datetime.now(timezone.utc).isoformat()
    payload = {
        "timestamp":   timestamp,
        "num_questions": len(EVAL_DATASET),
        "baseline":    {
            "config":  "dense-only (RAGService.retrieve)",
            "scores":  baseline_scores,
            "rows":    baseline_rows,
        },
        "enhanced":    {
            "config":  "hybrid BM25+dense + CrossEncoder reranking",
            "scores":  enhanced_scores,
            "rows":    enhanced_rows,
        },
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    log.info("Results saved to %s", output_path)


if __name__ == "__main__":
    main()
