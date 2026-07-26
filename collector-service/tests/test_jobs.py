from app.jobs import JobStore, PENDING, RUNNING, DONE, FAILED


async def test_create_returns_pending_job():
    store = JobStore()
    job = await store.create("echo", "hello")
    assert job.status == PENDING
    assert job.backend == "echo"
    assert job.query == "hello"


async def test_get_returns_none_for_unknown_id():
    store = JobStore()
    assert await store.get("nonexistent") is None


async def test_mark_running_updates_status():
    store = JobStore()
    job = await store.create("echo", "hello")
    await store.mark_running(job.id)
    fetched = await store.get(job.id)
    assert fetched.status == RUNNING


async def test_mark_done_stores_result():
    store = JobStore()
    job = await store.create("echo", "hello")
    await store.mark_done(job.id, [{"title": "x"}])
    fetched = await store.get(job.id)
    assert fetched.status == DONE
    assert fetched.result == [{"title": "x"}]


async def test_mark_failed_stores_error():
    store = JobStore()
    job = await store.create("echo", "hello")
    await store.mark_failed(job.id, "boom")
    fetched = await store.get(job.id)
    assert fetched.status == FAILED
    assert fetched.error == "boom"


async def test_jobs_are_isolated_by_id():
    store = JobStore()
    job_a = await store.create("echo", "a")
    job_b = await store.create("echo", "b")
    await store.mark_done(job_a.id, [{"title": "a"}])
    fetched_b = await store.get(job_b.id)
    assert fetched_b.status == PENDING
