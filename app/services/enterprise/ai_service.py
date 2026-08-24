import logging
import re
from typing import cast

from app.core.ai import generate_aptitude_questions, generate_coding_questions
from app.core.ai import generate_interview_questions as giq
from app.models.enterprise.assessment import AssessmentType

logger = logging.getLogger(__name__)

# CR/LF and control characters in a caller-supplied value let it forge extra log lines
# (log injection), so strip them and cap the length before anything reaches the log.
_LOG_UNSAFE = re.compile(r"[\r\n\x00-\x1f\x7f]")


def _log_safe(value: object, max_len: int = 32) -> str:
    return _LOG_UNSAFE.sub("", str(value or ""))[:max_len]


async def generate_assessment_questions(
    type: AssessmentType, topic: str, count: int = 10, language: str = "English"
) -> list[dict[str, object]]:
    """Generate assessment questions using the LLM, retrying once if the first try is empty.

    A single LLM call can occasionally come back unparseable or with every option-answer
    dropped (more likely in non-English output), which would hand the caller an empty
    assessment. One retry makes that self-heal instead of surfacing a blank test.
    """
    for attempt in range(2):
        result = await _generate_assessment_questions_once(type, topic, count, language)
        if result:
            return result
        if attempt == 0:
            logger.warning(
                "Assessment generation returned 0 questions (%s, %s); retrying once",
                type,
                _log_safe(language),
            )
    return []


async def _generate_assessment_questions_once(
    type: AssessmentType, topic: str, count: int = 10, language: str = "English"
) -> list[dict[str, object]]:
    # Don't interpolate the user-supplied topic into logs (log-injection); count + type suffice.
    logger.info("Generating %s %s questions", count, type)

    difficulty = "Medium"
    context = f"Topic: {topic}. Assessment for a professional role."

    try:
        if type == AssessmentType.VIDEO:
            # Open-ended prompts the candidate answers on camera (no options/correct answer);
            # reuse the conversational interview-question generator.
            raw_questions = await giq(topic, count, "Intermediate", context, language=language)
            return [
                {
                    "id": str(i),
                    "type": "VIDEO",
                    "question": cast("str", q.get("question") or q.get("question_text") or ""),
                    "expected_answer_points": q.get("expected_answer_points", []),
                }
                for i, q in enumerate(raw_questions, 1)
                if (q.get("question") or q.get("question_text"))
            ]
        if type == AssessmentType.APTITUDE:
            raw_questions = await generate_aptitude_questions(
                topic, count, difficulty, context, language=language
            )
            return [
                {
                    "id": str(i),
                    "type": "APTITUDE",
                    "question": cast("str", q.get("question_text", "")),
                    "options": q.get("options", []),
                    "correct_answer": q.get("correct_answer", ""),
                    "explanation": q.get("explanation", ""),
                }
                for i, q in enumerate(raw_questions, 1)
            ]
        if type == AssessmentType.CODING:
            raw_questions = await generate_coding_questions(
                topic, count, difficulty, context, language=language
            )
            return [
                {
                    "id": str(i),
                    "type": "CODING",
                    "title": q.get("title", ""),
                    "problem_statement": q.get("question_text", ""),
                    "content": {
                        "problem_description": cast("dict[str, object]", q.get("content", {})).get(
                            "problem_description", ""
                        ),
                        "constraints": cast("dict[str, object]", q.get("content", {})).get("constraints", []),
                        "examples": cast("dict[str, object]", q.get("content", {})).get("examples", []),
                        "test_cases": cast("dict[str, object]", q.get("content", {})).get("test_cases", []),
                        "initial_code": cast("dict[str, object]", q.get("content", {})).get(
                            "initial_code", {}
                        ),
                    },
                    "difficulty": q.get("difficulty", "Medium"),
                }
                for i, q in enumerate(raw_questions, 1)
            ]
        # BOTH
        apt_count = count // 2
        cod_count = count - apt_count

        apt_raw = await generate_aptitude_questions(topic, apt_count, difficulty, context, language=language)
        cod_raw = await generate_coding_questions(topic, cod_count, difficulty, context, language=language)

        questions: list[dict[str, object]] = []
        # Map Aptitude
        for i, q in enumerate(apt_raw, 1):
            questions.append(
                {
                    "id": f"apt_{i}",
                    "type": "APTITUDE",
                    "question": q.get("question_text", ""),
                    "options": q.get("options", []),
                    "correct_answer": q.get("correct_answer", ""),
                    "explanation": q.get("explanation", ""),
                }
            )
        # Map Coding
        for i, q in enumerate(cod_raw, 1):
            questions.append(
                {
                    "id": f"cod_{i}",
                    "type": "CODING",
                    "title": q.get("title", ""),
                    "problem_statement": q.get("question_text", ""),
                    "content": {
                        "problem_description": cast("dict[str, object]", q.get("content", {})).get(
                            "problem_description", ""
                        ),
                        "constraints": cast("dict[str, object]", q.get("content", {})).get("constraints", []),
                        "examples": cast("dict[str, object]", q.get("content", {})).get("examples", []),
                        "test_cases": cast("dict[str, object]", q.get("content", {})).get("test_cases", []),
                        "initial_code": cast("dict[str, object]", q.get("content", {})).get(
                            "initial_code", {}
                        ),
                    },
                    "difficulty": q.get("difficulty", "Medium"),
                }
            )
        return questions
    except Exception as e:
        logger.error(f"AI Generation Error: {e}")
        return []


async def generate_interview_questions_service(
    topic: str, count: int = 10, difficulty: str = "Intermediate", language: str = "English"
) -> list[dict[str, object]]:
    """
    Service wrapper for generating interview questions.
    """
    # Don't interpolate user-supplied topic/difficulty into logs (log-injection).
    logger.info("Generating %s interview questions", count)
    return await giq(topic, count, difficulty, language=language)
