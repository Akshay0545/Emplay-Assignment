"""Answer the Q&A log questions with the QA graph and write eval/qa_log.md (BUILD_PLAN 7.3).

The first five questions are the brief's examples; the rest exercise single,
comparison and change questions across both bids. Every answer is written
with its citations (file, page, quote) and confidence.

    python -m eval.run_qa_log
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from rfp_intel.agents.graph import run_question
from rfp_intel.agents.runtime import close_engine, get_engine
from rfp_intel.schemas import Answer

from eval.loaders import EVAL_DIR

QA_LOG_PATH = EVAL_DIR / "qa_log.md"

# (question, bid ids or None for all indexed bids)
QUESTIONS: list[tuple[str, list[str] | None]] = [
    ("What is the submission deadline for Bid1 after all addendums?", ["Bid1"]),
    ("Which affidavits are required for the Dell laptop bid?", ["Bid2"]),
    ("Compare the warranty requirements of both bids.", None),
    ("Is a bid bond required, and if so, how much?", None),
    ("What changed in Addendum 2 compared to the original RFP?", ["Bid1"]),
    ("Is there a pre-proposal meeting for Bid1, and is attendance mandatory?", ["Bid1"]),
    ("How must proposals be submitted for Bid2, and are email submissions allowed?", ["Bid2"]),
    ("What is the delivery requirement for the Dell laptops?", ["Bid2"]),
    ("Which cooperatives or master contracts can be used in each bid?", None),
    ("What are the memory and storage requirements for the Tier 3 student laptop?", ["Bid1"]),
    ("Who is the procurement contact for each bid?", None),
    ("Is etching required on monitors in Bid1?", ["Bid1"]),
]


def answer_all(questions: list[tuple[str, list[str] | None]] = QUESTIONS) -> list[Answer]:
    """Run the QA graph for every question (all indexed bids when none are given)."""
    known = get_engine().known_bids()
    answers: list[Answer] = []
    for question, bids in questions:
        state = run_question(question, bids or known)
        answers.append(state["answer"])
    return answers


def render(answers: list[Answer]) -> str:
    """Markdown log with citations."""
    lines = ["# Q&A log", "", f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by the QA graph "
             "(Orchestrator -> Retrieval -> Q&A agent -> Validator). Questions 1-5 are the brief's examples.", ""]
    for i, ans in enumerate(answers, start=1):
        lines += [f"## {i}. {ans.question}", "", f"*Bids: {', '.join(ans.bids)} | confidence {ans.confidence:.2f}*", "",
                  ans.answer, ""]
        if ans.citations:
            lines.append("Citations:")
            for n, c in enumerate(ans.citations, start=1):
                quote = c.quote.replace("\n", " ")
                lines.append(f"{n}. {c.file}, p.{c.page}: \"{quote[:220]}{'...' if len(quote) > 220 else ''}\"")
        else:
            lines.append("Citations: none")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    """Write the log and print a one-line summary per question."""
    try:
        answers = answer_all()
    finally:
        close_engine()
    QA_LOG_PATH.write_text(render(answers), encoding="utf-8")
    for ans in answers:
        print(f"[{ans.confidence:.2f}] {ans.question} -> {ans.answer[:90]!r} ({len(ans.citations)} citations)")
    print(f"written: {QA_LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
