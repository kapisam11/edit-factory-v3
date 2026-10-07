        {},
        str(tmp_path / "output"),
        str(appmod.DB_PATH),
    )

    assert captured["topic"] == "V3 worker"
    assert captured["kwargs"]["platform"] == "youtube_shorts"
    assert captured["kwargs"]["audience"] == "general short-form viewers"
    job = appmod.db_get_job("job-v3-worker")
    assert job["status"] == "done"
    published_package = Path(job["pkg_dir"])
    assert published_package != Path(captured["package_dir"])
    assert (published_package / "v3_job_result.json").is_file()
