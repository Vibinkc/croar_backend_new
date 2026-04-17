import logging
from typing import Any

from app.models.enterprise.assessment import AssessmentType

logger = logging.getLogger(__name__)

from app.core.ai import generate_aptitude_questions, generate_coding_questions


async def generate_assessment_questions(
    type: AssessmentType, topic: str, count: int = 10
) -> list[dict[str, Any]]:
    """
    Generates assessment questions using LLM.
    """
    logger.info(f"Generating {count} {type} questions for topic: {topic}")

    difficulty = "Medium"
    context = f"Topic: {topic}. Assessment for a professional role."

    try:
        if type == AssessmentType.APTITUDE:
            raw_questions = await generate_aptitude_questions(topic, count, difficulty, context)
            return [
                {
                    "id": str(i),
                    "type": "APTITUDE",
                    "question": q["question_text"],
                    "options": q["options"],
                    "correct_answer": q["correct_answer"],
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
                    "title": q["title"],
                    "problem_statement": q["question_text"],
                    "content": {
                        "problem_description": q["content"].get("problem_description", ""),
                        "constraints": q["content"].get("constraints", []),
                        "examples": q["content"].get("examples", []),
                        "test_cases": q["content"].get("test_cases", []),
                        "initial_code": q["content"].get("initial_code", {}),
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

        questions = []
        # Map Aptitude
        for i, q in enumerate(apt_raw, 1):
            questions.append(
                {
                    "id": f"apt_{i}",
                    "type": "APTITUDE",
                    "question": q["question_text"],
                    "options": q["options"],
                    "correct_answer": q["correct_answer"],
                    "explanation": q.get("explanation", ""),
                }
            )
        # Map Coding
        for i, q in enumerate(cod_raw, 1):
            questions.append(
                {
                    "id": f"cod_{i}",
                    "type": "CODING",
                    "title": q["title"],
                    "problem_statement": q["question_text"],
                    "content": {
                        "problem_description": q["content"].get("problem_description", ""),
                        "constraints": q["content"].get("constraints", []),
                        "examples": q["content"].get("examples", []),
                        "test_cases": q["content"].get("test_cases", []),
                        "initial_code": q["content"].get("initial_code", {}),
                    },
                    "difficulty": q.get("difficulty", "Medium"),
                }
            )
        return questions
    except Exception as e:
        logger.error(f"AI Generation Error: {e}")
        return []


from app.core.ai import generate_interview_questions as giq


async def generate_interview_questions_service(
    topic: str, count: int = 10, difficulty: str = "Intermediate"
) -> list[dict[str, Any]]:
    """
    Service wrapper for generating interview questions.
    """
    logger.info(f"Generating {count} interview questions for topic: {topic} (Difficulty: {difficulty})")
    return await giq(topic, count, difficulty)
