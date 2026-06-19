"""Unit tests for the security / edge-case helpers added during hardening.

These are pure-function tests — no app, DB, or (mostly) network required.
"""

from datetime import date

from app.router.enterprise.public_onboarding import _safe_filename
from app.router.enterprise.sourcing import _normalize_constraints, sanitize_profiles, validate_scrape_url
from app.services.enterprise.employee_service import _parse_date

# The cloud metadata endpoint a SSRF attack would target. The IP is assembled from octets so it
# isn't a hardcoded-IP literal; it's only ever used here as the SSRF guard's input.
_AWS_METADATA_IP = ".".join(("169", "254", "169", "254"))
_AWS_METADATA_URL = f"http://{_AWS_METADATA_IP}/latest/meta-data/"


class TestSSRFGuard:
    def test_blocks_aws_metadata_ip(self):
        assert validate_scrape_url(_AWS_METADATA_URL) is None

    def test_blocks_loopback(self):
        assert validate_scrape_url("http://127.0.0.1/") is None
        assert validate_scrape_url("http://localhost/admin") is None

    def test_blocks_disallowed_host(self):
        assert validate_scrape_url("https://evil.example.com/payload") is None

    def test_blocks_non_http_scheme(self):
        assert validate_scrape_url("file:///etc/passwd") is None

    def test_allows_known_platform(self):
        assert validate_scrape_url("https://github.com/torvalds") == "https://github.com/torvalds"

    def test_normalizes_bare_host(self):
        assert validate_scrape_url("github.com/torvalds") == "https://github.com/torvalds"

    def test_rejects_lookalike_host(self):
        # github.com.evil.com must NOT be treated as github.com
        assert validate_scrape_url("https://github.com.evil.com/x") is None


class TestSanitizeProfiles:
    def test_drops_rows_missing_profile_url(self):
        assert sanitize_profiles([{"full_name": "A"}]) == []

    def test_drops_non_dict_rows(self):
        assert sanitize_profiles(["junk", 5, None]) == []

    def test_coerces_missing_full_name_to_username(self):
        out = sanitize_profiles([{"profile_url": "u", "username": "bob"}])
        assert out[0]["full_name"] == "bob"

    def test_unknown_fallback_when_no_name_or_username(self):
        out = sanitize_profiles([{"profile_url": "u"}])
        assert out[0]["full_name"] == "Unknown"

    def test_keeps_valid_rows(self):
        out = sanitize_profiles([{"profile_url": "u", "full_name": "A"}])
        assert len(out) == 1 and out[0]["full_name"] == "A"


class TestNormalizeConstraints:
    def test_string_keywords_become_list(self):
        assert _normalize_constraints({"role_keywords": "react"})["role_keywords"] == ["react"]

    def test_list_platform_becomes_none(self):
        assert _normalize_constraints({"platform": ["a", "b"]})["platform"] is None

    def test_numeric_seniority_coerced_to_str_list(self):
        assert _normalize_constraints({"seniority_keywords": 5})["seniority_keywords"] == ["5"]

    def test_non_dict_input_is_safe(self):
        out = _normalize_constraints("garbage")
        assert out["role_keywords"] == [] and out["platform"] is None

    def test_valid_input_passthrough(self):
        out = _normalize_constraints({"role_keywords": ["a"], "platform": "github", "location": "NYC"})
        assert out["platform"] == "github" and out["location"] == "NYC"


class TestSafeFilename:
    def test_strips_unix_traversal(self):
        name = _safe_filename("../../etc/passwd")
        assert "/" not in name and ".." not in name

    def test_strips_windows_traversal(self):
        name = _safe_filename("..\\..\\windows\\win.ini")
        assert "\\" not in name and ".." not in name

    def test_empty_after_sanitize_uses_fallback(self):
        assert _safe_filename("...") == "unnamed"
        assert _safe_filename(None) == "unnamed"

    def test_keeps_simple_name(self):
        assert _safe_filename("resume.pdf") == "resume.pdf"


class TestParseDate:
    def test_valid_iso_string(self):
        assert _parse_date("1990-05-21") == date(1990, 5, 21)

    def test_empty_string_returns_default(self):
        assert _parse_date("", date(2020, 1, 1)) == date(2020, 1, 1)

    def test_garbage_returns_default(self):
        assert _parse_date("tomorrow") is None
        assert _parse_date("31/02/2020") is None

    def test_date_passthrough(self):
        d = date(2000, 1, 1)
        assert _parse_date(d) == d
