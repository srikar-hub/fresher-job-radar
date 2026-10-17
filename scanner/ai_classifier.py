"""Validation and policy safeguards for optional, advisory AI assessments."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from scanner.job_filter import Classification, JobAssessment, JobFilter
from scanner.job_parser import JobPost
from scanner.job_filter import _experience_state

MAX_INPUT_CHARS = 6000
MAX_EVIDENCE = 5


@dataclass(frozen=True, slots=True)
class AIResult:
    decision: Classification
    reason: str
    evidence: tuple[str, ...]
    confidence: float
    missing_information: tuple[str, ...]
    provider: str
    model: str
    assessed_at: str


class AIResponseError(ValueError):
    """Provider response was malformed or not grounded in the post."""


def assessment_input(post: JobPost, *, max_input_chars: int = MAX_INPUT_CHARS) -> tuple[str, dict[str, Any]]:
    if not 256 <= max_input_chars <= MAX_INPUT_CHARS:
        raise ValueError(f"max_input_chars must be between 256 and {MAX_INPUT_CHARS}")
    text = post.raw_text[:max_input_chars]
    def visible(value: str | None) -> str | None:
        return value if value and value.casefold() in text.casefold() else None
    value = {
        "title": visible(post.title),
        "company": visible(post.company),
        "description": text,
        "experience_requirement": visible(post.experience_requirement),
        "graduation_batch_requirements": [year for year in post.eligible_batches if str(year) in text],
        "location": visible(post.location),
        "work_arrangement": "remote" if "remote" in text.casefold() else None,
        "application_url": visible(post.application_urls[0]) if post.application_urls else None,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), value


def validate_result(raw: Any, post: JobPost, *, source_text: str | None = None) -> AIResult:
    if not isinstance(raw, Mapping):
        raise AIResponseError("Invalid AI assessment")
    if set(raw) - {"decision", "reason", "evidence", "confidence", "missing_information", "provider", "model", "assessed_at"}:
        raise AIResponseError("Invalid AI assessment")
    decision = raw.get("decision")
    if decision not in {item.value for item in Classification}:
        raise AIResponseError("Invalid AI assessment")
    reason = raw.get("reason")
    evidence = raw.get("evidence")
    confidence = raw.get("confidence")
    missing = raw.get("missing_information")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise AIResponseError("Invalid AI assessment")
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE or any(not isinstance(x, str) or not x.strip() or len(x) > 240 for x in evidence):
        raise AIResponseError("Invalid AI assessment")
    if any(x.casefold() not in (source_text if source_text is not None else post.raw_text).casefold() for x in evidence):
        raise AIResponseError("Invalid AI assessment")
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise AIResponseError("Invalid AI assessment")
    if not isinstance(missing, list) or len(missing) > 8 or any(not isinstance(x, str) or not x.strip() or len(x) > 160 for x in missing):
        raise AIResponseError("Invalid AI assessment")
    if decision in {Classification.MATCHED.value, Classification.REJECTED.value} and not evidence:
        raise AIResponseError("Invalid AI assessment")
    now = datetime.now(timezone.utc).isoformat()
    assessed_at = raw.get("assessed_at", now)
    if not isinstance(assessed_at, str) or len(assessed_at) > 64:
        assessed_at = now
    return AIResult(Classification(decision), reason.strip(), tuple(evidence), float(confidence),
                    tuple(missing), str(raw.get("provider", "cloudflare-workers-ai"))[:80],
                    str(raw.get("model", "unknown"))[:120], assessed_at)


def apply_advisory_result(base: JobAssessment, result: AIResult, job_filter: JobFilter) -> JobAssessment:
    """Apply AI only when deterministic eligibility facts independently prove safety."""
    if base.classification is not Classification.REVIEW:
        return base
    if result.decision is Classification.MATCHED and _deterministically_eligible(base.post, job_filter):
        decision = Classification.MATCHED
    elif result.decision is Classification.REJECTED and _deterministically_ineligible(base.post, job_filter):
        decision = Classification.REJECTED
    else:
        decision = Classification.REVIEW
    note = f"Optional AI ({result.provider}/{result.model}, confidence {result.confidence:.2f}): {result.reason}"
    if decision is Classification.REVIEW:
        note += " Deterministic eligibility is not conclusive; retained for review."
    evidence = tuple(dict.fromkeys((*base.post.evidence, *result.evidence)))
    post = replace(base.post, evidence=evidence)
    return JobAssessment(post, decision, base.score, f"{base.explanation} {note}"[:4000],
                         (*base.reasons, note[:500]))


def _deterministically_eligible(post: JobPost, job_filter: JobFilter) -> bool:
    # A model cannot supply missing eligibility facts. Run the unchanged rules
    # on the original post; only an independently proven match may alert.
    return job_filter.classify(post).classification is Classification.MATCHED


def _deterministically_ineligible(post: JobPost, job_filter: JobFilter) -> bool:
    explicit_experience_exclusion = _experience_state(post.raw_text) == "ineligible"
    conflicting_batches = job_filter._batch_state(post) == "ineligible"
    conflicting_degree = job_filter._education_state(post) == "ineligible"
    return explicit_experience_exclusion or conflicting_batches or conflicting_degree
