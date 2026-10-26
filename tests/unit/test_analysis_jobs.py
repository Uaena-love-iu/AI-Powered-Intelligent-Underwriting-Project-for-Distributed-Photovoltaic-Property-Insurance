from app.storage import AnalysisJobRepository


def test_interrupted_in_process_job_is_marked_failed(tmp_path) -> None:
    repository = AnalysisJobRepository(tmp_path / "jobs.sqlite3")
    repository.create_job("job-001", "case-001")
    repository.update_job(
        "job-001",
        status="running",
        progress_percent=45,
        current_step="图片分析完成",
    )

    repository.fail_incomplete_jobs()

    job = repository.get_job("job-001")
    assert job is not None
    assert job["status"] == "failed"
    assert job["error_code"] == "SERVER_RESTARTED"
    assert job["current_step"] == "服务重启导致任务中断"
