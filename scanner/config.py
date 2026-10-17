"""Small environment configuration scaffold for Phase 1."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True, slots=True)
class Settings:
    """Non-secret runtime settings; service credentials are added in later phases."""

    app_env: str = "development"
    log_level: str = "INFO"
    ai_enabled: bool = False
    ai_max_requests_per_scan: int = 3
    ai_timeout_seconds: float = 8.0
    ai_max_input_chars: int = 6000

    @classmethod
    def from_env(cls) -> Settings:
        app_env = os.getenv("APP_ENV", "development").strip().lower()
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if not app_env:
            raise ValueError("APP_ENV cannot be empty")
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return cls(
            app_env=app_env,
            log_level=log_level,
            ai_enabled=_as_bool(os.getenv("AI_ENABLED", "false"), name="AI_ENABLED"),
            ai_max_requests_per_scan=_as_bounded_int("AI_MAX_REQUESTS_PER_SCAN", 3, 0, 20),
            ai_timeout_seconds=_as_bounded_float("AI_TIMEOUT_SECONDS", 8.0, 1.0, 15.0),
            ai_max_input_chars=_as_bounded_int("AI_MAX_INPUT_CHARS", 6000, 256, 6000),
        )


def _as_bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _as_bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be between {minimum} and {maximum} seconds") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum} seconds")
    return value


@dataclass(frozen=True, slots=True)
class JobProfile:
    """User-editable preferences used by the deterministic job filter."""

    graduation_years: tuple[int, ...] = (2026,)
    education_keywords: tuple[str, ...] = ("computer science", "computer science engineering", "cse")
    target_roles: tuple[str, ...] = (
        "software engineer", "software developer", "sde", "associate software engineer",
        "graduate software engineer", "junior software engineer", "software engineer trainee",
        "software development engineer", "graduate engineer trainee", "java developer", "python developer",
        "backend developer", "frontend developer", "full stack developer", "application developer", "web developer",
    )
    preferred_skills: tuple[str, ...] = (
        "Java", "Spring Boot", "REST API", "SQL", "MySQL", "PostgreSQL", "JavaScript",
        "React", "Git", "DSA", "Maven", "Gradle", "JUnit",
    )
    preferred_locations: tuple[str, ...] = ("Hyderabad", "Bengaluru", "Pune", "Chennai")
    allow_relocation: bool = True
    include_internships: bool = False

    def __post_init__(self) -> None:
        if not self.graduation_years or any(type(year) is not int or year < 1900 or year > 2100 for year in self.graduation_years):
            raise ValueError("graduation_years must contain valid years as integers")
        for field in ("education_keywords", "target_roles", "preferred_skills", "preferred_locations"):
            values = getattr(self, field)
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{field} must contain non-empty strings")
        if type(self.allow_relocation) is not bool or type(self.include_internships) is not bool:
            raise ValueError("allow_relocation and include_internships must be booleans")


def load_job_profile(path: str | Path | None = None) -> JobProfile:
    """Load preferences from JSON, defaulting to the checked-in editable profile."""
    selected_path = Path(path or os.getenv("JOB_PROFILE_FILE", "config/job_profile.json"))
    if not selected_path.is_absolute():
        selected_path = Path(__file__).resolve().parents[1] / selected_path
    data = json.loads(selected_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Job profile JSON must contain an object")
    allowed = set(JobProfile.__dataclass_fields__)
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown job profile setting(s): {', '.join(sorted(unknown))}")
    for field in ("graduation_years", "education_keywords", "target_roles", "preferred_skills", "preferred_locations"):
        if field in data:
            value = data[field]
            if not isinstance(value, list):
                raise ValueError(f"{field} must be a JSON array")
            data[field] = tuple(value)
    return JobProfile(**data)
