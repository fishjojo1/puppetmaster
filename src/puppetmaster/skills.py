from __future__ import annotations

import re

from .errors import PuppetError


SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def normalize_skill_name(skill_name: str | None) -> str | None:
    normalized = (skill_name or "").strip().lower()
    if not normalized:
        return None
    if not SKILL_NAME_PATTERN.match(normalized):
        raise PuppetError(
            "invalid_skill_name",
            "skill-name must start with a letter or number and contain only letters, numbers, dot, underscore, or hyphen.",
            "Use a short name like review, release-check, or test.plan.",
        )
    return normalized
