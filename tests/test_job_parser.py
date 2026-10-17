from datetime import datetime, timezone
from types import SimpleNamespace

from telethon.tl.types import MessageEntityTextUrl

from scanner.job_parser import parse_job_post


def test_extracts_job_fields_and_source_metadata_from_channel_message():
    posted = datetime(2026, 2, 1, tzinfo=timezone.utc)
    message = SimpleNamespace(
        message_id=42,
        channel_id=-100123,
        channel_name="Fresher Jobs",
        text="Acme is Hiring Freshers\nPosition: Software Engineer\nExperience: 0-1 years\nBatch: 2026\nLocation: Hyderabad\nSkills: Java, SQL\nApply by: 31 Dec 2026\nhttps://careers.example/job/12345",
        date=posted,
        urls=("https://careers.example/job/12345",),
        message_link="https://t.me/acmejobs/42",
    )

    job = parse_job_post(message)

    assert job.company == "Acme"
    assert job.title == "Software Engineer"
    assert job.experience_requirement == "Experience: 0-1 years"
    assert job.eligible_batches == (2026,)
    assert job.location == "Hyderabad"
    assert "Java" in job.skills and "SQL" in job.skills
    assert job.application_urls == ("https://careers.example/job/12345",)
    assert job.deadline == "31 Dec 2026"
    assert (job.source_channel_id, job.source_message_id, job.posted_at) == (-100123, 42, posted)
    assert job.original_message_link == "https://t.me/acmejobs/42"
    assert job.raw_text.startswith("Acme is Hiring")


def test_extracts_multiple_urls_and_hidden_telegram_hyperlink():
    text = "Role: Python Developer\nApply at https://jobs.example/visible"
    entity = MessageEntityTextUrl(offset=0, length=4, url="https://jobs.example/hidden")

    job = parse_job_post(text, entities=[entity])

    assert job.application_urls == ("https://jobs.example/visible", "https://jobs.example/hidden")


def test_missing_values_remain_unknown_without_invention():
    job = parse_job_post("Hiring now for a future role; more details soon.")

    assert job.company is None
    assert job.title is None
    assert job.deadline is None
    assert job.application_urls == ()


def test_detects_multiple_distinct_position_labels():
    job = parse_job_post("Position: Software Engineer\nRole: Java Developer\nBatch: 2026")

    assert job.multiple_positions is True
