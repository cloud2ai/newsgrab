"""FastAPI app: health check + the async job API (POST /jobs, GET /jobs/{id})."""
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException

from app.collectors.base import COLLECTORS
from app.jobs import JobStore
from app.schemas import HealthResponse, JobCreatedResponse, JobRequest, JobStatusResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="newsgrab collector-service")
job_store = JobStore()


async def _run_job(job_id: str, backend: str, query: str, params: dict) -> None:
    await job_store.mark_running(job_id)
    try:
        collector = COLLECTORS[backend]
        result = await collector(query, **params)
    except Exception as exc:
        logger.warning("[main] job %s failed: %s", job_id, exc)
        await job_store.mark_failed(job_id, str(exc))
        return
    await job_store.mark_done(job_id, result)


@app.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/jobs", response_model=JobCreatedResponse, status_code=201)
async def create_job(payload: JobRequest, background_tasks: BackgroundTasks) -> JobCreatedResponse:
    if payload.backend not in COLLECTORS:
        raise HTTPException(status_code=400, detail=f"unknown backend: {payload.backend}")
    job = await job_store.create(payload.backend, payload.query)
    background_tasks.add_task(_run_job, job.id, payload.backend, payload.query, payload.params)
    return JobCreatedResponse(job_id=job.id)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str) -> JobStatusResponse:
    job = await job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatusResponse(job_id=job.id, status=job.status, result=job.result, error=job.error)
