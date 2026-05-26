from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sysconfig

from .errors import PuppetError


SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
SUBAGENT_SKILL_PREFIX = "subagent-"


@dataclass(frozen=True)
class SubagentSkill:
    name: str
    description: str
    body: str
    path: Path


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


def source_skills_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "skills"


def installed_skills_directory() -> Path:
    return Path(sysconfig.get_path("data")) / "share" / "puppetmaster" / "skills"


def skills_directories() -> list[Path]:
    candidates = [source_skills_directory(), installed_skills_directory()]
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            unique.append(candidate)
            seen.add(resolved)
    return unique


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    frontmatter_text = text[4:end]
    body = text[end + len("\n---") :].lstrip("\r\n")
    frontmatter: dict[str, str] = {}
    for raw_line in frontmatter_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        frontmatter[key.strip()] = value.strip().strip("\"'")
    return frontmatter, body


def list_subagent_skill_records(directory: Path | None = None) -> list[SubagentSkill]:
    roots = [directory] if directory else skills_directories()
    skills: list[SubagentSkill] = []
    seen_names: set[str] = set()
    for root in roots:
        if root is None or not root.exists():
            continue
        for path in sorted(root.glob(f"{SUBAGENT_SKILL_PREFIX}*.md")):
            name = path.stem
            normalized = normalize_skill_name(name)
            if normalized is None or not normalized.startswith(SUBAGENT_SKILL_PREFIX) or normalized in seen_names:
                continue
            text = path.read_text(encoding="utf-8")
            frontmatter, body = _parse_frontmatter(text)
            description = (frontmatter.get("description") or "").strip()
            if not description:
                continue
            skills.append(SubagentSkill(name=normalized, description=description, body=body.strip(), path=path))
            seen_names.add(normalized)
    return skills


def list_subagent_skills(directory: Path | None = None) -> list[dict[str, str]]:
    return [{"name": skill.name, "description": skill.description} for skill in list_subagent_skill_records(directory)]


def subagent_skill(skill_name: str | None, directory: Path | None = None) -> SubagentSkill | None:
    normalized = normalize_skill_name(skill_name)
    if normalized is None:
        return None
    if not normalized.startswith(SUBAGENT_SKILL_PREFIX):
        raise PuppetError(
            "invalid_subagent_skill",
            "skill must name a subagent skill.",
            "Use list_subagent_skills and pass a skill name beginning with subagent-.",
        )
    for skill in list_subagent_skill_records(directory):
        if skill.name == normalized:
            return skill
    raise PuppetError(
        "unknown_subagent_skill",
        f"Unknown subagent skill: {normalized}",
        "Use list_subagent_skills to see available subagent skills.",
    )
