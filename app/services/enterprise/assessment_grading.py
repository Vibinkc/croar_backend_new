"""Shared grading for generated-question assessments (aptitude + coding).

Extracted so both the candidate-facing take flow and the employee skill-assessment
flow score identically: aptitude/MCQ is a deterministic correct-answer match; coding
is delegated to the AI evaluator. Returns (overall, aptitude_score, coding_score),
each a 0-100 int (aptitude/coding may be None when the test has none of that type).
"""

from typing import Any, cast

from app.services.enterprise.ai_evaluator import ai_evaluator_service


async def grade_assessment(
    questions: list[dict[str, Any]], answers: dict[str, Any]
) -> tuple[int, int | None, int | None]:
    apt_correct = 0
    apt_total = 0
    cod_score_accum = 0.0
    cod_total = 0

    for q in questions:
        q_id = str(q.get("id"))
        q_type = str(q.get("type") or "").upper()
        ans_val = answers.get(q_id)

        if q_type in ("APTITUDE", "MCQ"):
            apt_total += 1
            # A blank/absent answer is never correct.
            if ans_val not in (None, "") and str(ans_val) == str(q.get("correct_answer")):
                apt_correct += 1
        elif q_type in ("CODING", "CODE"):
            cod_total += 1
            code = str(ans_val or "").strip()
            # No code submitted → 0 for this question. Never send blank code to the AI
            # evaluator (it can hallucinate a non-zero score for an empty answer).
            if not code:
                continue
            problem = q.get("problem_statement") or q.get("text") or q.get("question") or ""
            content = q.get("content") if isinstance(q.get("content"), dict) else {}
            test_cases = (content or {}).get("test_cases") or q.get("test_cases", [])
            evaluation = await ai_evaluator_service.evaluate_code_response(
                str(problem), cast("list[dict[str, str]]", test_cases or []), code
            )
            try:
                cod_score_accum += float(evaluation.get("score", 0))
            except (TypeError, ValueError):
                pass

    apt_score = int((apt_correct / apt_total) * 100) if apt_total > 0 else None
    cod_score = int(cod_score_accum / cod_total) if cod_total > 0 else None

    parts = [s for s in (apt_score, cod_score) if s is not None]
    overall = sum(parts) // len(parts) if parts else 0
    return overall, apt_score, cod_score
