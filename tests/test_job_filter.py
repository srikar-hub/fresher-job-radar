import pytest

from scanner.config import JobProfile, load_job_profile
from scanner.job_filter import Classification, JobFilter
from scanner.job_parser import parse_job_post


def assess(text, *, profile=None):
    post = parse_job_post(text, profile=profile)
    return JobFilter(profile).classify(post)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hiring Software Engineer. Freshers can apply. Batch: 2026. B.Tech CSE.", Classification.MATCHED),
        ("Software Developer, 0-1 years experience. 2026 batch. CSE graduates.", Classification.MATCHED),
        ("SDE 1: 0–2 years. 2026 batch eligible. Computer Science degree.", Classification.MATCHED),
        ("Java Developer, 0-3 years experience. Batch 2026, CSE.", Classification.MATCHED),
        ("Software Engineer requires 2+ years experience. Batch 2026.", Classification.REJECTED),
        ("Backend Developer, 3-5 years of experience required. Batch 2026.", Classification.REJECTED),
        ("Python Developer, 5+ years required. 2026 graduates.", Classification.REJECTED),
        ("Hiring Associate Software Engineer. 0 years experience. 2026 batch eligible.", Classification.MATCHED),
        ("Software Engineer: 2025/2026 batches eligible; freshers welcome.", Classification.MATCHED),
        ("Software Developer. Freshers can apply. Batch: 2024 only.", Classification.REJECTED),
        ("Hiring Software Engineer for 2026 batch. Experience not mentioned.", Classification.REVIEW),
        ("Software Engineer, 2026 batch. Experience preferred.", Classification.REVIEW),
        ("Graduate Engineer Trainee (Software), 2026 batch, CSE graduates.", Classification.MATCHED),
        ("Senior Software Engineer hiring now. 2026 batch; freshers can apply.", Classification.REJECTED),
        ("Hiring Sales Executive. Freshers welcome. Batch 2026.", Classification.REJECTED),
        ("Python Developer requires 0-2 years and 3-5 years, 2026 batch.", Classification.REJECTED),
        ("Freshers can apply, but Software Developer needs 3-5 years mandatory. Batch 2026.", Classification.REVIEW),
        ("🎯 HIRING 🚀\n💼 Role: Software Engineer\n🎓 Batch: 2026\n✨ No experience required.", Classification.MATCHED),
        ("Role: Full Stack Developer\nBatch: 2026\nFreshers eligible. Apply at https://jobs.example/12345 and https://careers.example/req-99887", Classification.MATCHED),
        ("Software Engineer, 2026 batch. New grads encouraged to apply.", Classification.MATCHED),
        ("Hiring Remote Software Developer, 0 years required, 2026 CSE graduates.", Classification.MATCHED),
        ("Software Engineer, 0-1 years, 2026 batch, Mumbai.", Classification.MATCHED),
        ("Software Engineer internship for 2026 CSE students.", Classification.REVIEW),
        ("Join our paid training bootcamp for software development jobs. Pay a fee now.", Classification.REJECTED),
        ("Software Engineer, 0 years experience, 2026 batch, CSE graduates. Apply now.", Classification.MATCHED),
        ("Role: Software Engineer\nRole: Data Analyst\nFreshers eligible, Batch 2026.", Classification.REVIEW),
        ("Software Engineer, 1-3 years experience required, batch 2026.", Classification.REJECTED),
        ("Software Engineer, minimum 3 years required, 2026 graduates.", Classification.REJECTED),
        ("Software Engineer, 0-2 years, 2026 batch. Only ECE graduates.", Classification.REJECTED),
        ("Off-campus hiring: Software Engineer trainee, 2026 batch, CSE, no experience required.", Classification.MATCHED),
        ("Software Engineer, freshers are not eligible. Batch 2026.", Classification.REJECTED),
        ("I hope to become a software engineer; believe in yourself and keep learning.", Classification.REJECTED),
        ("Software Development Engineer, new grad, 2026 batch.", Classification.MATCHED),
        ("Software Engineer, no experience, 2026 batch. CSE candidates are not eligible.", Classification.REJECTED),
    ],
)
def test_realistic_classification_scenarios(text, expected):
    assert assess(text).classification is expected


def test_configurable_profile_can_change_batch_and_role_preferences():
    profile = JobProfile(graduation_years=(2027,), target_roles=("data engineer",))

    result = assess("Data Engineer, fresh graduates welcome, Batch 2027.", profile=profile)

    assert result.classification is Classification.MATCHED


def test_matched_result_has_bounded_non_probability_score_and_reasons():
    result = assess("Hiring Software Engineer. No experience required. Batch 2026. Java and SQL.")

    assert 0 <= result.score <= 100
    assert "compatible" in result.explanation
    assert result.reasons


def test_profile_loads_from_editable_json(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text('{"graduation_years": [2027], "allow_relocation": false}', encoding="utf-8")

    profile = load_job_profile(profile_path)

    assert profile.graduation_years == (2027,)
    assert profile.allow_relocation is False
