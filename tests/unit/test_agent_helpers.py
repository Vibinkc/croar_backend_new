"""Unit tests for the Croar Pilot agent helper functions (app/agents/tools.py).

All pure functions — no app, DB, or network. These are the defensive normalizers and
generators the LLM-facing tools rely on, so they must be rock-solid.
"""

from datetime import date

from app.agents.tools import (
    _clamp_int,
    _default_onboarding_form,
    _generate_time_slots,
    _norm_assessment_type,
    _norm_interview_type,
    _parse_date,
)
from app.models.enterprise.assessment import AssessmentType


class TestClampInt:
    def test_value_in_range_passes_through(self):
        assert _clamp_int(5, 1, 10, 3) == 5

    def test_below_low_clamps_to_low(self):
        assert _clamp_int(-5, 0, 60, 0) == 0

    def test_above_high_clamps_to_high(self):
        assert _clamp_int(999, 1, 50, 10) == 50

    def test_string_number_is_coerced(self):
        assert _clamp_int("7", 1, 50, 10) == 7

    def test_non_numeric_falls_back_to_default(self):
        assert _clamp_int("abc", 1, 50, 10) == 10

    def test_none_falls_back_to_default(self):
        assert _clamp_int(None, 1, 50, 10) == 10

    def test_float_is_truncated_then_clamped(self):
        assert _clamp_int(3.9, 1, 50, 10) == 3

    def test_boundaries_inclusive(self):
        assert _clamp_int(1, 1, 10, 5) == 1
        assert _clamp_int(10, 1, 10, 5) == 10


class TestNormInterviewType:
    def test_ai_variants(self):
        assert _norm_interview_type("AI") == "AI"
        assert _norm_interview_type("ai") == "AI"

    def test_human_and_meet_variants_become_gmeet(self):
        for v in ("GMEET", "human", "HUMAN", "google meet", "googlemeet", "meet", "panel", "live"):
            assert _norm_interview_type(v) == "GMEET", v

    def test_none_and_empty_default_to_ai(self):
        assert _norm_interview_type(None) == "AI"
        assert _norm_interview_type("") == "AI"

    def test_unknown_defaults_to_ai(self):
        assert _norm_interview_type("xyz") == "AI"

    def test_whitespace_is_tolerated(self):
        assert _norm_interview_type("  human  ") == "GMEET"


class TestNormAssessmentType:
    def test_coding(self):
        assert _norm_assessment_type("coding") == AssessmentType.CODING
        assert _norm_assessment_type("CODING") == AssessmentType.CODING

    def test_aptitude(self):
        assert _norm_assessment_type("aptitude") == AssessmentType.APTITUDE

    def test_both(self):
        assert _norm_assessment_type("both") == AssessmentType.BOTH
        assert _norm_assessment_type("BOTH") == AssessmentType.BOTH

    def test_none_defaults_to_both(self):
        assert _norm_assessment_type(None) == AssessmentType.BOTH

    def test_unknown_defaults_to_both(self):
        assert _norm_assessment_type("nonsense") == AssessmentType.BOTH
        assert _norm_assessment_type("") == AssessmentType.BOTH


class TestParseDate:
    def test_valid_iso(self):
        assert _parse_date("2026-06-20") == date(2026, 6, 20)

    def test_none_returns_none(self):
        assert _parse_date(None) is None

    def test_empty_returns_none(self):
        assert _parse_date("") is None

    def test_invalid_returns_none(self):
        assert _parse_date("not-a-date") is None
        assert _parse_date("2026-13-99") is None

    def test_surrounding_whitespace_is_stripped(self):
        assert _parse_date("  2026-01-01  ") == date(2026, 1, 1)


class TestGenerateTimeSlots:
    def test_basic_30min_five_slots(self):
        assert _generate_time_slots("09:00", "17:00", 30, 5) == ["09:00", "09:30", "10:00", "10:30", "11:00"]

    def test_45min_three_slots(self):
        assert _generate_time_slots("10:00", "18:00", 45, 3) == ["10:00", "10:45", "11:30"]

    def test_bounded_by_end_of_window(self):
        # 60-min slots from 09:00; last slot that still fits before 17:00 starts at 16:00.
        slots = _generate_time_slots("09:00", "17:00", 60, 100)
        assert slots[0] == "09:00"
        assert slots[-1] == "16:00"
        assert len(slots) == 8

    def test_bad_time_input_returns_empty(self):
        assert _generate_time_slots("", "", 30, 5) == []
        assert _generate_time_slots("9 oclock", "5pm", 30, 5) == []

    def test_zero_limit_returns_empty(self):
        assert _generate_time_slots("09:00", "17:00", 30, 0) == []

    def test_zero_duration_uses_default_interval(self):
        # duration 0 -> falls back to a 30-min interval, so it doesn't loop forever.
        slots = _generate_time_slots("09:00", "10:00", 0, 5)
        assert slots == ["09:00", "09:30"]


class TestDefaultOnboardingForm:
    def test_returns_sections_and_documents(self):
        sections, docs = _default_onboarding_form()
        assert isinstance(sections, list) and isinstance(docs, list)

    def test_expected_section_ids(self):
        sections, _ = _default_onboarding_form()
        ids = [s["id"] for s in sections]
        assert ids == ["personal_info", "job_info", "education_info", "bank_details", "documents"]

    def test_every_field_has_required_keys(self):
        sections, _ = _default_onboarding_form()
        for section in sections:
            assert {"id", "title", "fields"} <= section.keys()
            for field in section["fields"]:
                assert {"name", "label", "type", "required"} <= field.keys()

    def test_documents_section_uses_file_fields(self):
        sections, _ = _default_onboarding_form()
        docs_section = next(s for s in sections if s["id"] == "documents")
        assert all(f["type"] == "file" for f in docs_section["fields"])

    def test_required_documents_shape(self):
        _, docs = _default_onboarding_form()
        assert len(docs) >= 1
        for d in docs:
            assert {"name", "description"} <= d.keys()
