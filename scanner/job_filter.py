"""Rule-based job relevance, fresher eligibility, and transparent scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from scanner.config import JobProfile, load_job_profile
from scanner.job_parser import JobPost, _contains_term


class Classification(str, Enum):
    MATCHED = "MATCHED"
    REVIEW = "REVIEW"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    eligibility: int = 30
    role: int = 25
    batch: int = 20
    education: int = 10
    skills: int = 10
    location: int = 3
    application_url: int = 2

    def __post_init__(self) -> None:
        if min(self.eligibility, self.role, self.batch, self.education, self.skills, self.location, self.application_url) < 0:
            raise ValueError("Score weights cannot be negative")


@dataclass(frozen=True, slots=True)
class JobAssessment:
    post: JobPost
    classification: Classification
    score: int
    explanation: str
    reasons: tuple[str, ...]


_RANGE_RE = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_PLUS_RE = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)\s*\+\s*(?:years?|yrs?)\b", re.IGNORECASE)
_REQUIRED_SINGLE_RE = re.compile(
    r"(?:minimum|min\.?)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b|"
    r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\s+(?:of\s+)?(?:relevant\s+)?experience\s+(?:is\s+)?(?:required|must|mandatory)",
    re.IGNORECASE,
)
_CLEAR_FRESHER_RE = re.compile(
    r"\b(?:freshers?\s+(?:can\s+apply|eligible|welcome|may\s+apply)|"
    r"(?:no|zero)\s+(?:prior\s+)?experience\s+(?:required|needed|necessary)|"
    r"0\s*(?:years?|yrs?)\s+(?:of\s+)?experience\s+(?:required|needed)|"
    r"(?:new|recent)\s+(?:graduates?|grads?)\s+(?:can\s+apply|eligible|welcome)|"
    r"entry[- ]level|graduate\s+(?:trainee|program|programme)|"
    r"campus\s+(?:or\s+off[- ]campus\s+)?(?:drive|hiring|recruitment)|off[- ]campus\s+drive)\b",
    re.IGNORECASE,
)
_FRESHER_WORD_RE = re.compile(r"\bfreshers?\b|\bfresh\s+graduates?\b|\bnew\s+(?:graduates?|grads?)\b|\bentry[- ]level\b", re.IGNORECASE)
_FRESHER_EXCLUSION_RE = re.compile(
    r"\b(?:no\s+freshers?|freshers?\s+(?:are\s+)?(?:not\s+eligible|cannot\s+apply|can't\s+apply|not\s+accepted|not\s+allowed)|"
    r"not\s+open\s+to\s+freshers?|only\s+experienced\s+candidates?)\b",
    re.IGNORECASE,
)
_PREFERRED_EXPERIENCE_RE = re.compile(r"\bexperience\s+(?:is\s+)?preferred\b|\bpreferred\s+experience\b", re.IGNORECASE)
_JOB_SIGNAL_RE = re.compile(
    r"\b(hiring|recruit(?:ment|ing)|apply\b|job\s+opening|vacanc(?:y|ies)|position\s+open|looking\s+for|"
    r"off[- ]campus|campus\s+drive|graduate\s+(?:program|programme)|careers?)\b",
    re.IGNORECASE,
)
_PROMOTION_RE = re.compile(
    r"\b(?:paid\s+training\s+(?:course|program|programme|bootcamp)|pay\s+(?:a\s+)?fee|course\s+enroll(?:ment)?|"
    r"join\s+(?:our|the)\s+(?:course|bootcamp)|guaranteed\s+placement\s+course)\b",
    re.IGNORECASE,
)
_SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|principal|staff|director|manager|architect)\b", re.IGNORECASE)
_INTERNSHIP_RE = re.compile(r"\b(internship|intern)\b", re.IGNORECASE)
_NONTECH_TITLE_RE = re.compile(
    r"\b(?:sales|marketing|human resources|recruiter|accountant|finance analyst|"
    r"customer support|data entry|mechanical engineer|civil engineer|electrical engineer)\b",
    re.IGNORECASE,
)
_NON_CSE_ONLY_RE = re.compile(
    r"\b(?:only\s+(?:for\s+)?|degree\s+in\s+|graduates?\s+in\s+|(?:B\.?\s?Tech|B\.?\s?E\.?)\s+(?:in\s+)?)"
    r"(?:mechanical|civil|electrical|electronics|chemical|biotechnology|\bECE\b|\bEEE\b)\b|"
    r"\b(?:mechanical|civil|electrical|electronics|chemical|biotechnology|ECE|EEE)\s+(?:engineering\s+)?(?:graduates?|candidates?)\s+only\b|"
    r"\b(?:only\s+for\s+)?non[- ]?CSE\s+(?:graduates?|candidates?)\s+only\b|"
    r"\bCSE\s+(?:graduates?|candidates?)\s+(?:are\s+)?not\s+eligible\b",
    re.IGNORECASE,
)
_INDIA_LOCATION_RE = re.compile(
    r"\b(?:India|Hyderabad|Bengaluru|Bangalore|Pune|Chennai|Mumbai|Delhi|Gurugram|Gurgaon|"
    r"Noida|Kolkata|Ahmedabad|Jaipur|Kochi|Mysuru|Coimbatore|Thiruvananthapuram|remote)\b",
    re.IGNORECASE,
)


class JobFilter:
    def __init__(
        self,
        profile: JobProfile | None = None,
        weights: ScoreWeights | None = None,
    ) -> None:
        self.profile = profile or load_job_profile()
        self.weights = weights or ScoreWeights()

    def classify(self, post: JobPost) -> JobAssessment:
        text = post.raw_text
        reasons: list[str] = []
        role_relevant = self._role_relevant(post)
        job_signal = bool(_JOB_SIGNAL_RE.search(text)) or post.title_explicit or bool(
            post.title
            and (
                post.eligible_batches
                or _FRESHER_WORD_RE.search(text)
                or re.search(r"\bexperience\b|\byears?\b|\byrs?\b|\binternship\b", text, re.I)
            )
        )
        promotion = bool(_PROMOTION_RE.search(text))
        internship_only = bool(_INTERNSHIP_RE.search(text)) and not re.search(r"\b(full[- ]time|permanent)\b", text, re.I)
        senior = bool(_SENIOR_RE.search(post.title or "")) or bool(
            re.search(r"\b(?:senior|sr\.?|lead|principal|staff|director|manager|architect)\b.{0,35}\b(?:engineer|developer|SDE)\b", text, re.I)
        )
        nontechnical = bool(_NONTECH_TITLE_RE.search(post.title or ""))
        experience_state = _experience_state(text)
        batch_state = self._batch_state(post)
        education_state = self._education_state(post)

        if promotion:
            reasons.append("The post promotes paid training or a course rather than a job.")
            return self._result(post, Classification.REJECTED, reasons, role_relevant, experience_state, batch_state, education_state)
        if not job_signal:
            reasons.append("No job-opening or application evidence was found.")
            return self._result(post, Classification.REJECTED, reasons, role_relevant, experience_state, batch_state, education_state)
        if post.multiple_positions:
            reasons.append("The post lists multiple positions with potentially different requirements.")
            return self._result(post, Classification.REVIEW, reasons, role_relevant, experience_state, batch_state, education_state)
        if nontechnical or not role_relevant:
            reasons.append("The advertised role is not a configured software-development target role.")
            return self._result(post, Classification.REJECTED, reasons, role_relevant, experience_state, batch_state, education_state)
        if senior:
            reasons.append("The title indicates a senior, lead, staff, principal, or management role.")
            return self._result(post, Classification.REJECTED, reasons, role_relevant, experience_state, batch_state, education_state)
        if experience_state == "ineligible":
            reasons.append("The stated minimum experience excludes a zero-experience candidate.")
            return self._result(post, Classification.REJECTED, reasons, role_relevant, experience_state, batch_state, education_state)
        if batch_state == "ineligible":
            reasons.append("The stated graduation batches do not include the configured target batch.")
            return self._result(post, Classification.REJECTED, reasons, role_relevant, experience_state, batch_state, education_state)
        if education_state == "ineligible":
            reasons.append("The mandatory education requirement excludes the configured degree profile.")
            return self._result(post, Classification.REJECTED, reasons, role_relevant, experience_state, batch_state, education_state)

        if internship_only and not self.profile.include_internships:
            reasons.append("Internship-only opportunities are set to review by default.")
            return self._result(post, Classification.REVIEW, reasons, role_relevant, experience_state, batch_state, education_state)
        if experience_state == "unclear":
            reasons.append("Experience is described as preferred or requirements conflict; verify fresher eligibility.")
            return self._result(post, Classification.REVIEW, reasons, role_relevant, experience_state, batch_state, education_state)
        if experience_state == "unknown":
            reasons.append("No explicit fresher or experience eligibility was found.")
            return self._result(post, Classification.REVIEW, reasons, role_relevant, experience_state, batch_state, education_state)
        if batch_state == "unknown":
            reasons.append("Graduation batch eligibility is not stated.")
            return self._result(post, Classification.REVIEW, reasons, role_relevant, experience_state, batch_state, education_state)
        if education_state == "unknown":
            reasons.append("Education eligibility is not explicit; review before applying.")
            return self._result(post, Classification.REVIEW, reasons, role_relevant, experience_state, batch_state, education_state)

        reasons.append("Software role, fresher eligibility, target batch, and education profile are compatible.")
        if post.skills:
            reasons.append(f"Relevant skills found: {', '.join(post.skills)}.")
        if post.application_urls:
            reasons.append("An application URL is present; whether it is open has not been verified.")
        if post.location and not _contains_term(post.location, "remote"):
            reasons.append("Location does not restrict eligibility because relocation is allowed.")
        return self._result(post, Classification.MATCHED, reasons, role_relevant, experience_state, batch_state, education_state)

    def _role_relevant(self, post: JobPost) -> bool:
        source = post.title or post.raw_text
        return any(_contains_term(source, role) for role in self.profile.target_roles)

    def _batch_state(self, post: JobPost) -> str:
        if not post.eligible_batches:
            return "unknown"
        return "eligible" if set(post.eligible_batches).intersection(self.profile.graduation_years) else "ineligible"

    def _education_state(self, post: JobPost) -> str:
        text = f"{post.raw_text}\n{post.education_requirement or ''}"
        if _NON_CSE_ONLY_RE.search(text):
            return "ineligible"
        if post.education_requirement is None:
            if re.search(r"\b(?:CSE|computer science|B\.?\s?Tech|B\.?\s?E\.?)\b", text, re.I):
                return "eligible"
            # No stated education restriction is not evidence of incompatibility.
            return "eligible"
        if any(_contains_term(text, keyword) for keyword in self.profile.education_keywords):
            return "eligible"
        if re.search(r"\b(?:any|all)\s+(?:engineering\s+)?(?:graduate|degree|discipline)s?\b", text, re.I):
            return "eligible"
        if re.search(r"\b(?:B\.?\s?Tech|B\.?\s?E\.?|bachelor)\b", text, re.I):
            return "unknown"
        return "unknown"

    def _result(
        self,
        post: JobPost,
        classification: Classification,
        reasons: list[str],
        role_relevant: bool,
        experience_state: str,
        batch_state: str,
        education_state: str,
    ) -> JobAssessment:
        score = _score(post, self.profile, self.weights, role_relevant, experience_state, batch_state, education_state)
        return JobAssessment(post, classification, score, " ".join(reasons), tuple(reasons))


def _experience_state(text: str) -> str:
    lowered = text.casefold()
    if _FRESHER_EXCLUSION_RE.search(text):
        return "ineligible"
    clear_acceptance = bool(_CLEAR_FRESHER_RE.search(text))
    explicit_zero = bool(re.search(
        r"\b(?:no|zero)\s+(?:prior\s+)?experience\b|\b0\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience\b|(?:required|needed)\b)",
        text,
        re.I,
    ))
    preferred = bool(_PREFERRED_EXPERIENCE_RE.search(text))
    found_range = list(_RANGE_RE.finditer(text))
    found_plus = list(_PLUS_RE.finditer(text))
    found_required = list(_REQUIRED_SINGLE_RE.finditer(text))

    if found_range:
        states: list[str] = []
        for match in found_range:
            minimum = float(match.group(1))
            if minimum == 0:
                states.append("eligible")
            elif minimum == 1 and clear_acceptance:
                states.append("eligible")
            elif preferred:
                states.append("unclear")
            elif clear_acceptance:
                states.append("unclear")
            else:
                states.append("ineligible")
        if "ineligible" in states:
            return "ineligible"
        if "unclear" in states:
            return "unclear"
        return "eligible"

    if found_plus or found_required:
        minimums = [float(match.group(1)) for match in found_plus]
        minimums.extend(float(value) for match in found_required for value in match.groups() if value)
        minimum = max(minimums, default=0)
        if minimum >= 2:
            return "unclear" if explicit_zero else "ineligible"
        if minimum > 0:
            return "eligible" if clear_acceptance else "ineligible"

    if preferred:
        return "eligible" if clear_acceptance or explicit_zero else "unclear"
    if clear_acceptance or explicit_zero or _FRESHER_WORD_RE.search(lowered):
        return "eligible"
    # Graduate programs/campus recruitment are positive only when tied to a target batch.
    if re.search(r"\b(?:graduate\s+(?:engineer\s+)?trainee|campus\s+(?:drive|hiring)|off[- ]campus\s+drive)\b", text, re.I):
        return "eligible"
    return "unknown"


def _score(
    post: JobPost,
    profile: JobProfile,
    weights: ScoreWeights,
    role_relevant: bool,
    experience_state: str,
    batch_state: str,
    education_state: str,
) -> int:
    total = sum((weights.eligibility, weights.role, weights.batch, weights.education, weights.skills, weights.location, weights.application_url))
    if total == 0:
        return 0
    points = 0.0
    points += weights.eligibility * (1 if experience_state == "eligible" else 0.4 if experience_state in {"unknown", "unclear"} else 0)
    points += weights.role * (1 if role_relevant else 0)
    points += weights.batch * (1 if batch_state == "eligible" else 0.4 if batch_state == "unknown" else 0)
    points += weights.education * (1 if education_state == "eligible" else 0.4 if education_state == "unknown" else 0)
    if profile.preferred_skills:
        overlap = sum(1 for skill in profile.preferred_skills if skill in post.skills)
        points += weights.skills * min(1, overlap / min(3, len(profile.preferred_skills)))
    if post.location:
        if any(_contains_term(post.location, location) for location in profile.preferred_locations):
            points += weights.location
        elif profile.allow_relocation and _INDIA_LOCATION_RE.search(post.location):
            points += weights.location * 0.75
        elif _contains_term(post.location, "remote"):
            points += weights.location
    if post.application_urls:
        points += weights.application_url
    # Normalize to 100 so callers can tune weights without changing output scale.
    return max(0, min(100, round(points * 100 / total)))


def classify_job(post: JobPost, profile: JobProfile | None = None) -> JobAssessment:
    """Convenience function for one post using the configured profile."""
    return JobFilter(profile).classify(post)
