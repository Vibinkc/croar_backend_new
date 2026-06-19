import logging
from typing import cast

from app.core.ai import generate_aptitude_questions, generate_coding_questions
from app.core.ai import generate_interview_questions as giq
from app.models.enterprise.assessment import AssessmentType

logger = logging.getLogger(__name__)


async def generate_assessment_questions(
    type: AssessmentType, topic: str, count: int = 10
) -> list[dict[str, object]]:
    """
    Generates assessment questions using LLM.
    """
    # Don't interpolate the user-supplied topic into logs (log-injection); count + type suffice.
    logger.info("Generating %s %s questions", count, type)

    difficulty = "Medium"
    context = f"Topic: {topic}. Assessment for a professional role."

    try:
        if type == AssessmentType.APTITUDE:
            raw_questions = await generate_aptitude_questions(topic, count, difficulty, context)
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
            raw_questions = await generate_coding_questions(topic, count, difficulty, context)
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

        apt_raw = await generate_aptitude_questions(topic, apt_count, difficulty, context)
        cod_raw = await generate_coding_questions(topic, cod_count, difficulty, context)

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
    topic: str, count: int = 10, difficulty: str = "Intermediate"
) -> list[dict[str, object]]:
    """
    Service wrapper for generating interview questions.
    """
    # Don't interpolate user-supplied topic/difficulty into logs (log-injection).
    logger.info("Generating %s interview questions", count)
    return await giq(topic, count, difficulty)
