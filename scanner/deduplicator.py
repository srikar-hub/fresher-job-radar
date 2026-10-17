"""Conservative in-memory duplicate detection for parsed job posts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from scanner.job_parser import JobPost

_TRACKING_KEYS = {"fbclid", "gclid", "dclid", "msclkid", "gh_src", "ref", "referrer", "source", "sourceid"}
_GENERIC_PATHS = {"", "/", "/jobs", "/careers", "/careers/", "/job-search", "/search"}
_REQ_ID_RE = re.compile(
    r"\b(?:gh_jid|job[_ ]?id|req(?:uisition)?[_ ]?id|job|req|requisition|opening|position)(?:\s*(?:id|#))?\s*[:=#/_-]*\s*([A-Z0-9][A-Z0-9-]{3,})\b|/(\d{5,})(?:[/?#]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    is_duplicate: bool
    reason: str


def normalize_application_url(url: str) -> str:
    """Normalize host and remove common tracking parameters while preserving job IDs."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{host}{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS
    ]
    query.sort()
    return urlunsplit((parts.scheme.casefold() or "https", netloc, path, urlencode(query), ""))


def extract_requisition_id(post: JobPost) -> str | None:
    for source in (*post.application_urls, post.raw_text):
        match = _REQ_ID_RE.search(source)
        if match:
            return (match.group(1) or match.group(2)).casefold()
    return None


def compare_jobs(left: JobPost, right: JobPost) -> DuplicateMatch:
    """Compare two posts; dissimilar explicit requisition IDs prevent a merge."""
    left_req = extract_requisition_id(left)
    right_req = extract_requisition_id(right)
    if left_req and right_req and left_req != right_req:
        return DuplicateMatch(False, "Different requisition identifiers")

    left_urls = {_specific_url(url) for url in left.application_urls}
    right_urls = {_specific_url(url) for url in right.application_urls}
    left_urls.discard(None)
    right_urls.discard(None)
    if left_urls.intersection(right_urls):
        return DuplicateMatch(True, "Same normalized application URL")

    left_company, right_company = _normalize_text(left.company), _normalize_text(right.company)
    if left_req and right_req and left_req == right_req and left_company and left_company == right_company:
        return DuplicateMatch(True, "Same company and requisition ID")

    left_text = _normalize_post_text(left.raw_text)
    right_text = _normalize_post_text(right.raw_text)
    if len(left_text) >= 35 and left_text == right_text:
        return DuplicateMatch(True, "Same normalized post text")

    left_title, right_title = _normalize_text(left.title), _normalize_text(right.title)
    left_location, right_location = _normalize_text(left.location), _normalize_text(right.location)
    if (
        left_company
        and left_company == right_company
        and left_title
        and left_title == right_title
        and left_location
        and left_location == right_location
    ):
        return DuplicateMatch(True, "Same company, title, and location")
    return DuplicateMatch(False, "No conservative duplicate signature matched")


class DuplicateDetector:
    """In-memory registry; persistent cross-run storage is intentionally out of scope."""

    def __init__(self) -> None:
        self._posts: list[JobPost] = []

    def find(self, post: JobPost) -> DuplicateMatch | None:
        for existing in self._posts:
            result = compare_jobs(existing, post)
            if result.is_duplicate:
                return result
        return None

    def add(self, post: JobPost) -> DuplicateMatch | None:
        """Return the matching reason if seen; otherwise remember and return None."""
        duplicate = self.find(post)
        if duplicate is None:
            self._posts.append(post)
        return duplicate

    def __len__(self) -> int:
        return len(self._posts)


def _specific_url(url: str) -> str | None:
    normalized = normalize_application_url(url)
    parts = urlsplit(normalized)
    if parts.hostname in {"t.me", "telegram.me", "www.linkedin.com", "linkedin.com", "facebook.com", "www.facebook.com"}:
        return None
    if parts.path.casefold() in _GENERIC_PATHS and not parts.query:
        return None
    return normalized


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _normalize_post_text(value: str) -> str:
    without_urls = re.sub(r"https?://\S+", " ", value, flags=re.IGNORECASE)
    return _normalize_text(without_urls)
