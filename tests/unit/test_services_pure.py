"""Unit tests for pure (no-DB, no-network) logic in the service layer.

Targets the deterministic helpers that the integration tests only exercise indirectly:
interview time parsing, onboarding code generation, employee date parsing, automation criteria
evaluation, and the sourcing provider registry.
"""

import re
from datetime import date

from app.services.enterprise.automation_service import evaluate_criteria
from app.services.enterprise.employee_service import _parse_date
from app.services.enterprise.interview_service import parse_time
from app.services.enterprise.onboarding_service import generate_onboarding_code
from app.services.enterprise.sourcing_service import SourcingService


class TestParseTime:
    def test_valid_hh_mm(self):
        t = parse_time("14:30")
        assert (t.hour, t.minute) == (14, 30)

    def test_midnight(self):
        t = parse_time("00:00")
        assert (t.hour, t.minute) == (0, 0)

    def test_garbage_falls_back_to_9am(self):
        assert (parse_time("not-a-time").hour, parse_time("not-a-time").minute) == (9, 0)

    def test_out_of_range_falls_back(self):
        # 25:99 parses as ints but time() rejects it -> safe 09:00 default.
        assert (parse_time("25:99").hour, parse_time("25:99").minute) == (9, 0)

    def test_empty_falls_back(self):
        assert parse_time("").hour == 9


class TestOnboardingCode:
    def test_format(self):
        assert re.fullmatch(r"ONB-\d{5}", generate_onboarding_code())

    def test_reasonably_unique(self):
        codes = {generate_onboarding_code() for _ in range(200)}
        # Cryptographically random 5-digit suffix — collisions across 200 draws are unlikely.
        assert len(codes) > 190


class TestParseDate:
    def test_passthrough_date(self):
        d = date(2025, 1, 2)
        assert _parse_date(d) == d

    def test_iso_string(self):
        assert _parse_date("2025-06-19") == date(2025, 6, 19)

    def test_iso_string_with_time_suffix(self):
        assert _parse_date("2025-06-19T10:00:00") == date(2025, 6, 19)

    def test_bad_string_returns_default(self):
        sentinel = date(1999, 1, 1)
        assert _parse_date("not-a-date", sentinel) == sentinel

    def test_empty_returns_default(self):
        assert _parse_date("", None) is None

    def test_none_returns_default(self):
        assert _parse_date(None) is None


class TestEvaluateCriteria:
    async def test_empty_criteria_is_true(self):
        assert await evaluate_criteria("", {}) is True
        assert await evaluate_criteria("   ", {}) is True

    async def test_ai_score_greater_than_pass(self):
        assert await evaluate_criteria("ai_score > 80", {"ai_score": 90}) is True

    async def test_ai_score_greater_than_fail(self):
        assert await evaluate_criteria("ai_score > 80", {"ai_score": 70}) is False

    async def test_ai_score_less_than(self):
        assert await evaluate_criteria("ai_score < 50", {"ai_score": 40}) is True
        assert await evaluate_criteria("ai_score < 50", {"ai_score": 60}) is False

    async def test_missing_score_defaults_to_zero(self):
        assert await evaluate_criteria("ai_score > 10", {}) is False

    async def test_numeric_gate_without_operator_fails_closed(self):
        # "ai_score" mentioned but no parseable >/< -> must NOT fire the action.
        assert await evaluate_criteria("ai_score", {"ai_score": 100}) is False

    async def test_non_numeric_criteria_is_true(self):
        assert await evaluate_criteria("looks like a strong candidate", {}) is True


class _FakeProvider:
    def __init__(self, rows):
        self._rows = rows

    def search(self, query, location, page, page_size):
        return self._rows


class TestSourcingRegistry:
    def test_unknown_platform_returns_empty(self):
        svc = SourcingService()
        assert svc.search("does-not-exist", "python") == []

    def test_register_and_search(self):
        svc = SourcingService()
        rows = [{"name": "Ada", "raw_data": {"x": 1}}]
        svc.register_provider("fake", _FakeProvider(rows))
        assert svc.search("FAKE", "engineer") == rows  # case-insensitive lookup

    def test_register_overrides_existing(self):
        svc = SourcingService()
        svc.register_provider("github", _FakeProvider([{"name": "stub"}]))
        assert svc.search("github", "q") == [{"name": "stub"}]

    def test_default_providers_present(self):
        svc = SourcingService()
        for key in ("github", "linkedin", "stackoverflow"):
            assert key in svc.providers
