"""Unit tests for the shared assessment grader (`grade_assessment`).

Covers the deterministic aptitude scoring and the coding path (AI evaluator mocked),
plus the important guard added after the "blank submission scored 69%" bug: an empty
answer must score 0 and must NEVER be sent to the AI evaluator.
"""

from app.services.enterprise import assessment_grading
from app.services.enterprise.assessment_grading import grade_assessment


def _apt(qid: str, correct: str) -> dict:
    return {
        "id": qid,
        "type": "APTITUDE",
        "question": "?",
        "options": ["A", "B", "C", "D"],
        "correct_answer": correct,
    }


def _cod(qid: str) -> dict:
    return {"id": qid, "type": "CODING", "problem_statement": "Reverse a string"}


class TestAptitude:
    async def test_all_correct_is_100(self):
        qs = [_apt("a", "B"), _apt("b", "C")]
        assert await grade_assessment(qs, {"a": "B", "b": "C"}) == (100, 100, None)

    async def test_half_correct_is_50(self):
        qs = [_apt("a", "B"), _apt("b", "C")]
        assert await grade_assessment(qs, {"a": "B", "b": "WRONG"}) == (50, 50, None)

    async def test_blank_answer_never_correct(self):
        qs = [_apt("a", "B")]
        # Missing key, empty string, and None are all "not answered".
        assert await grade_assessment(qs, {}) == (0, 0, None)
        assert await grade_assessment(qs, {"a": ""}) == (0, 0, None)


class TestBlankSubmission:
    async def test_blank_submission_scores_zero(self, monkeypatch):
        """The regression that caused the bug: an all-blank submission must be 0,
        and the AI evaluator must not be invoked for the (blank) coding question."""
        called = {"hit": False}

        async def _boom(*_a, **_k):
            called["hit"] = True
            return {"score": 99}

        monkeypatch.setattr(assessment_grading.ai_evaluator_service, "evaluate_code_response", _boom)

        qs = [_apt("a", "B"), _cod("c")]
        assert await grade_assessment(qs, {}) == (0, 0, 0)
        assert called["hit"] is False  # blank code never reaches the model

    async def test_whitespace_only_code_scores_zero(self, monkeypatch):
        async def _boom(*_a, **_k):
            raise AssertionError("AI evaluator must not be called for blank code")

        monkeypatch.setattr(assessment_grading.ai_evaluator_service, "evaluate_code_response", _boom)
        qs = [_cod("c")]
        overall, apt, cod = await grade_assessment(qs, {"c": "   \n  "})
        assert (overall, apt, cod) == (0, None, 0)


class TestCoding:
    async def test_coding_uses_evaluator_score(self, monkeypatch):
        async def _fake(*_a, **_k):
            return {"score": 80}

        monkeypatch.setattr(assessment_grading.ai_evaluator_service, "evaluate_code_response", _fake)
        qs = [_cod("c")]
        overall, apt, cod = await grade_assessment(qs, {"c": "def f(): return 1"})
        assert (overall, apt, cod) == (80, None, 80)

    async def test_both_types_average(self, monkeypatch):
        async def _fake(*_a, **_k):
            return {"score": 100}

        monkeypatch.setattr(assessment_grading.ai_evaluator_service, "evaluate_code_response", _fake)
        # Aptitude wrong (0) + coding perfect (100) -> overall average 50.
        qs = [_apt("a", "B"), _cod("c")]
        overall, apt, cod = await grade_assessment(qs, {"a": "WRONG", "c": "some code"})
        assert (overall, apt, cod) == (50, 0, 100)
