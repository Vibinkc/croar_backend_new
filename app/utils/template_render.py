"""Centralized email-template placeholder rendering.

Handles ``{{ key }}`` style placeholders robustly:
  - optional surrounding whitespace inside the braces (``{{key}}`` == ``{{ key }}``
    == ``{{  key  }}``)
  - case-insensitive key matching (``{{Candidate_Name}}`` == ``{{candidate_name}}``)

Used by every outbound send path (manual send endpoint + automated/scheduled/
assessment emails) so substitution behaves identically everywhere.
"""

from __future__ import annotations

import re

# Matches {{ key }} where key is a word (letters/digits/underscore), allowing any
# amount of whitespace between the braces and the key.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_template(text: str | None, variables: dict[str, object]) -> str:
    """Replace every ``{{ key }}`` placeholder in ``text`` with its value.

    - Whitespace inside the braces is ignored.
    - Key matching is case-insensitive.
    - Unknown placeholders are left untouched (so existing links handled
      elsewhere, e.g. ``{{assessment_link}}``, are not clobbered if absent here).
    """
    if not text:
        return text or ""

    # Normalize the lookup table once: lower-cased keys -> stringified values.
    lookup = {str(k).lower(): ("" if v is None else str(v)) for k, v in variables.items()}

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        if key in lookup:
            return lookup[key]
        # Leave unknown placeholders as-is.
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, text)


def build_candidate_variables(full_name: str | None) -> dict[str, str]:
    """Build the standard set of name aliases from a candidate's full name.

    All of ``candidate_name``, ``full_name``, ``name`` map to the full name;
    ``first_name`` is the first whitespace-delimited token.
    """
    name = (full_name or "").strip() or "Candidate"
    first_name = name.split()[0] if name.split() else name
    return {"candidate_name": name, "full_name": name, "name": name, "first_name": first_name}
