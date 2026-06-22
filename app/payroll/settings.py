"""Settings adapter for the ported payroll module.

The payroll services were written against a settings object exposing
``smtp_host``, ``smtp_from_email``, ``smtp_from_name``, ``smtp_use_ssl`` (plus
the JWT fields). Croar's settings name these slightly differently (and omit a
couple), so this thin wrapper exposes the payroll-shaped attribute names backed
by Croar's :func:`app.core.settings.get_settings`.
"""

from app.core.settings import get_settings

_croar = get_settings()


class _PayrollSettings:
    """Payroll-named view over Croar's settings (read-only)."""

    # ----- JWT -----
    @property
    def secret_key(self) -> str:
        return _croar.secret_key

    @property
    def jwt_algorithm(self) -> str:
        return _croar.algorithm

    @property
    def access_token_expire_minutes(self) -> int:
        return _croar.access_token_expire_minutes

    # ----- SMTP (email_service) -----
    @property
    def smtp_host(self) -> str | None:
        # Croar calls this smtp_address.
        return getattr(_croar, "smtp_address", None)

    @property
    def smtp_port(self) -> int:
        return getattr(_croar, "smtp_port", 587)

    @property
    def smtp_username(self) -> str | None:
        return getattr(_croar, "smtp_username", None)

    @property
    def smtp_password(self) -> str | None:
        return getattr(_croar, "smtp_password", None)

    @property
    def smtp_from_email(self) -> str | None:
        # Fall back to the SMTP username when no dedicated from-address is set.
        return getattr(_croar, "smtp_from_email", None) or getattr(_croar, "smtp_username", None)

    @property
    def smtp_from_name(self) -> str:
        return getattr(_croar, "smtp_from_name", None) or "Payroll"

    @property
    def smtp_use_ssl(self) -> bool:
        return bool(getattr(_croar, "smtp_use_ssl", False))


Settings = _PayrollSettings  # payroll imported the class and instantiated it
settings = _PayrollSettings()
