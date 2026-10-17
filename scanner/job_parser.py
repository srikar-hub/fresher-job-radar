"""Deterministic extraction of job facts from Telegram text and message objects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from scanner.config import JobProfile, load_job_profile

_URL_RE = re.compile(r"https?://[^\s<>\]\[()]+", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_EXPERIENCE_RE = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b(?:\s+(?:of\s+)?(?:relevant\s+)?experience)?",
    re.IGNORECASE,
)
_SINGLE_EXPERIENCE_RE = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?)\s*\+\s*(?:years?|yrs?)\b(?:\s+(?:of\s+)?experience)?|"
    r"(?:minimum|min\.?\s*)(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(r"^\s*(company|employer)\s*[:：-]\s*(.+?)\s*$", re.IGNORECASE)
_TITLE_LABEL_RE = re.compile(r"^\s*(?:job\s*)?(?:title|role|position|designation)\s*[:：-]\s*(.+?)\s*$", re.IGNORECASE)
_DEADLINE_RE = re.compile(r"^\s*(?:application\s+)?(?:last\s+date|deadline|apply\s+by)\s*[:：-]?\s*(.+?)\s*$", re.IGNORECASE)
_LOCATION_RE = re.compile(r"^\s*(?:job\s+)?location\s*[:：-]\s*(.+?)\s*$", re.IGNORECASE)
_EDUCATION_RE = re.compile(r"^\s*(?:education|qualification|eligibility)\s*[:：-]\s*(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class JobPost:
    raw_text: str
    company: str | None = None
    title: str | None = None
    title_explicit: bool = False
    experience_requirement: str | None = None
    eligible_batches: tuple[int, ...] = ()
    education_requirement: str | None = None
    location: str | None = None
    skills: tuple[str, ...] = ()
    application_urls: tuple[str, ...] = ()
    deadline: str | None = None
    source_channel_id: int | None = None
    source_channel_name: str | None = None
    source_message_id: int | None = None
    original_message_link: str | None = None
    posted_at: datetime | None = None
    multiple_positions: bool = False
    evidence: tuple[str, ...] = ()


def parse_job_post(
    message: Any,
    *,
    entities: Iterable[Any] | None = None,
    profile: JobProfile | None = None,
) -> JobPost:
    """Extract evidenced fields from raw text or a Phase 2 ChannelMessage.

    Unknown fields remain ``None`` (or empty tuples); no company, date, or
    eligibility details are inferred without text evidence.
    """
    profile = profile or load_job_profile()
    if isinstance(message, str):
        text = message
        metadata: dict[str, Any] = {}
        known_urls: Iterable[str] = ()
    else:
        text = getattr(message, "text", None) or getattr(message, "message", None) or ""
        metadata = {
            "source_channel_id": getattr(message, "channel_id", None),
            "source_channel_name": getattr(message, "channel_name", None),
            "source_message_id": getattr(message, "message_id", getattr(message, "id", None)),
            "original_message_link": getattr(message, "message_link", None),
            "posted_at": getattr(message, "date", None),
        }
        known_urls = getattr(message, "urls", ()) or ()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    company: str | None = None
    titles: list[str] = []
    locations: list[str] = []
    educations: list[str] = []
    deadlines: list[str] = []
    experience_lines: list[str] = []
    for line in lines:
        field_line = re.sub(r"^[^\w]+", "", line, flags=re.UNICODE)
        if found := _LABEL_RE.match(field_line):
            company = _clean_field(found.group(2))
        if found := _TITLE_LABEL_RE.match(field_line):
            titles.append(_clean_field(found.group(1)))
        if found := _LOCATION_RE.match(field_line):
            locations.append(_clean_field(found.group(1)))
        if found := _EDUCATION_RE.match(field_line):
            educations.append(_clean_field(found.group(1)))
        if found := _DEADLINE_RE.match(field_line):
            deadlines.append(_clean_field(found.group(1)))
        if re.search(r"\bexperience\b|\byears?\b|\byrs?\b", line, re.IGNORECASE) or re.match(
            r"^\s*(?:freshers?|entry[- ]level)\b", line, re.IGNORECASE
        ):
            experience_lines.append(line)

    if not company:
        company_match = re.search(
            r"^\s*([^\n:]{2,80}?)\s+(?:is\s+)?(?:hiring|recruiting|invites? applications?)\b",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        if company_match:
            company = _clean_field(company_match.group(1))

    title_explicit = bool(titles)
    if not titles:
        title = _infer_title(text, profile.target_roles)
        if title:
            titles.append(title)
    distinct_titles = list(dict.fromkeys(title for title in titles if title))
    multiple_positions = len(distinct_titles) > 1 or _has_multiple_position_headings(lines)
    title = distinct_titles[0] if distinct_titles else None

    batch_years: set[int] = set()
    for match in _YEAR_RE.finditer(text):
        start, end = match.span()
        context = text[max(0, start - 32) : min(len(text), end + 32)]
        if re.search(r"\b(batch(?:es)?|graduation|graduates?|graduat(?:e|es|ing)|pass(?:ing)?[- ]?outs?|class\s+of)\b", context, re.IGNORECASE):
            batch_years.add(int(match.group(1)))

    urls: list[str] = list(_URL_RE.findall(text))
    urls.extend(_entity_urls(text, entities))
    urls.extend(str(url) for url in known_urls if url)
    application_urls = tuple(dict.fromkeys(_trim_url(url) for url in urls if _trim_url(url)))
    skills = tuple(skill for skill in profile.preferred_skills if _contains_term(text, skill))
    location = locations[0] if locations else _infer_location(text, profile)
    evidence = _evidence_lines(lines)

    return JobPost(
        raw_text=text,
        company=company,
        title=title,
        title_explicit=title_explicit,
        experience_requirement="; ".join(dict.fromkeys(experience_lines)) or None,
        eligible_batches=tuple(sorted(batch_years)),
        education_requirement="; ".join(dict.fromkeys(educations)) or _infer_education_requirement(text),
        location=location,
        skills=skills,
        application_urls=application_urls,
        deadline=deadlines[0] if deadlines else None,
        multiple_positions=multiple_positions,
        evidence=evidence,
        **metadata,
    )


def _clean_field(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t-*•") or ""


def _contains_term(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE))


def _infer_title(text: str, roles: Iterable[str]) -> str | None:
    for role in sorted(roles, key=len, reverse=True):
        match = re.search(rf"(?<!\w){re.escape(role)}(?!\w)", text, re.IGNORECASE)
        if match:
            line_start = text.rfind("\n", 0, match.start()) + 1
            prefix = text[line_start : match.start()].strip(" \t:-*•")
            if prefix and re.fullmatch(r"(?:senior|sr\.?|lead|principal|staff|junior|associate|graduate|trainee)", prefix, re.I):
                return f"{prefix.strip()} {match.group(0).strip()}"
            return match.group(0).strip()
    return None


def _has_multiple_position_headings(lines: list[str]) -> bool:
    headings = [line for line in lines if re.match(r"^(?:\d+[.)]|[-•])\s+.{4,}", line)]
    relevant = [line for line in headings if re.search(r"engineer|developer|analyst|intern|scientist|architect", line, re.I)]
    return len(relevant) > 1


def _entity_urls(text: str, entities: Iterable[Any] | None) -> list[str]:
    extracted: list[str] = []
    for entity in entities or ():
        hidden = getattr(entity, "url", None)
        if hidden:
            extracted.append(str(hidden))
        elif getattr(entity, "offset", None) is not None and getattr(entity, "length", None):
            candidate = text[entity.offset : entity.offset + entity.length]
            if candidate.lower().startswith(("http://", "https://")):
                extracted.append(candidate)
    return extracted


def _trim_url(url: str) -> str:
    return url.rstrip(".,;:!?)]}")


def _infer_location(text: str, profile: JobProfile) -> str | None:
    candidates = (*profile.preferred_locations, "Remote", "India", "Mumbai", "Delhi", "Gurugram", "Gurgaon", "Noida", "Kolkata", "Ahmedabad", "Jaipur", "Kochi", "Mysuru", "Coimbatore", "Thiruvananthapuram")
    for candidate in sorted(candidates, key=len, reverse=True):
        if _contains_term(text, candidate):
            return candidate
    return None


def _infer_education_requirement(text: str) -> str | None:
    match = re.search(r"(?:B\.?\s?Tech|B\.?\s?E\.?|Bachelor(?:'s)?)\b[^\n]{0,100}", text, re.IGNORECASE)
    return _clean_field(match.group(0)) if match else None


def _evidence_lines(lines: list[str]) -> tuple[str, ...]:
    evidence: list[str] = []
    evidence_pattern = re.compile(
        r"hiring|fresh|experience|\byears?\b|\byrs?\b|batch|graduate|pass.?out|"
        r"engineer|developer|\bSDE\b|apply|location|qualification|\bCSE\b|\bB\.?Tech\b",
        re.IGNORECASE,
    )
    for line in lines:
        if evidence_pattern.search(line) and line not in evidence:
            evidence.append(line)
    return tuple(evidence)
