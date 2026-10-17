from scanner.deduplicator import DuplicateDetector, compare_jobs, normalize_application_url
from scanner.job_parser import parse_job_post


def post(text, *, title=None, company=None, location=None, urls=()):
    job = parse_job_post(text)
    if title or company or location or urls:
        from dataclasses import replace
        job = replace(job, title=title or job.title, company=company or job.company,
                      location=location or job.location, application_urls=urls or job.application_urls)
    return job


def test_duplicate_posts_from_different_channels_match_by_application_url():
    first = post("Software Engineer", urls=("https://careers.example/jobs/req-12345",))
    second = post("Apply for this role", urls=("https://careers.example/jobs/req-12345",))

    result = compare_jobs(first, second)

    assert result.is_duplicate
    assert result.reason == "Same normalized application URL"


def test_tracking_parameters_are_removed_from_application_url():
    one = normalize_application_url("https://WWW.Example.com/job/123?utm_source=telegram&gh_src=abc&job=123#apply")
    two = normalize_application_url("https://example.com/job/123?job=123")

    assert one == two


def test_same_company_title_and_location_is_duplicate():
    first = post("first", company="Acme", title="Software Engineer", location="Hyderabad")
    second = post("second channel post", company="ACME", title="software engineer", location="Hyderabad")

    assert compare_jobs(first, second).is_duplicate


def test_different_jobs_at_same_company_are_not_merged():
    first = post("first", company="Acme", title="Software Engineer", location="Hyderabad")
    second = post("second", company="Acme", title="Backend Developer", location="Hyderabad")

    assert not compare_jobs(first, second).is_duplicate


def test_different_requisition_ids_prevent_merge():
    first = post("Req ID: 12345", company="Acme", title="Software Engineer", location="Pune")
    second = post("Req ID: 67890", company="Acme", title="Software Engineer", location="Pune")

    assert not compare_jobs(first, second).is_duplicate


def test_detector_stores_only_unique_posts():
    detector = DuplicateDetector()
    first = post("Same announcement content with enough words to be a complete normalized job post.")
    second = post("Same announcement content with enough words to be a complete normalized job post.")

    assert detector.add(first) is None
    assert detector.add(second).is_duplicate
    assert len(detector) == 1
