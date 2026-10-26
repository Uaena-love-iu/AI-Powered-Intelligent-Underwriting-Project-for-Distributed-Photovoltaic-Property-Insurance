import sqlite3
from datetime import UTC, datetime, timedelta

from app.storage import AnalysisJobRepository, CaseRepository


def test_retention_purges_expired_case_review_and_job_data(tmp_path) -> None:
    database_path = tmp_path / "cases.sqlite3"
    cases = CaseRepository(database_path)
    jobs = AnalysisJobRepository(database_path)
    cases.list_cases(limit=1)
    old_time = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    recent_time = datetime.now(UTC).isoformat()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO cases (case_id, stored_at, decision, review_status, case_json)
            VALUES ('expired', ?, 'manual_review', 'completed', '{}'),
                   ('recent', ?, 'accept', 'pending', '{}')
            """,
            (old_time, recent_time),
        )
        connection.execute(
            """
            INSERT INTO review_events (
                case_id, reviewer_name, final_decision, comment, reviewed_at
            ) VALUES ('expired', 'reviewer', 'accept', 'done', ?)
            """,
            (old_time,),
        )

    jobs.create_job("old-job", "expired")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE analysis_jobs SET updated_at = ? WHERE job_id = 'old-job'",
            (old_time,),
        )

    removed = cases.purge_expired_cases(
        retention_days=30,
        now=datetime.now(UTC),
    )

    assert removed == 1
    assert cases.get_case("expired") is None
    assert [case["case_id"] for case in cases.list_cases()] == ["recent"]
    assert jobs.get_job("old-job") is None


def test_existing_case_database_adds_separate_review_pricing(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript("""
            CREATE TABLE cases (
                case_id TEXT PRIMARY KEY, stored_at TEXT NOT NULL, decision TEXT,
                review_status TEXT NOT NULL, reviewer_name TEXT, final_decision TEXT,
                review_comment TEXT, reviewed_at TEXT, case_json TEXT NOT NULL
            );
            CREATE TABLE review_events (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL REFERENCES cases(case_id), reviewer_name TEXT NOT NULL,
                final_decision TEXT NOT NULL, comment TEXT NOT NULL,
                reviewed_at TEXT NOT NULL
            );
            INSERT INTO cases (case_id, stored_at, decision, review_status, case_json)
            VALUES ('legacy', '2026-10-01', 'accept', 'pending', '{}');
        """)
    cases = CaseRepository(database_path)
    reviewed = cases.submit_review(
        case_id="legacy", reviewer_name="核保员", final_decision="conditional_accept",
        pricing_recommendation="surcharge", comment="确认条件与价格依据",
    )
    assert reviewed["final_decision"] == "conditional_accept"
    assert reviewed["pricing_recommendation"] == "surcharge"
    assert reviewed["review_history"][0]["pricing_recommendation"] == "surcharge"
    assert cases.list_cases()[0]["system_pricing_recommendation"] == "not_proposed"
