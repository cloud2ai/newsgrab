"""In-memory job bookkeeping.

Not persisted -- a restart loses all jobs, by design (this service is
stateless for article data; job status is request-lifecycle-scoped
bookkeeping, not durable business data callers should rely on surviving
a restart).
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


@dataclass
class Job:
    id: str
    backend: str
    query: str
    status: str = PENDING
    created_at: float = field(default_factory=time.time)
    result: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def create(self, backend: str, query: str) -> Job:
        job = Job(id=str(uuid.uuid4()), backend=backend, query=query)
        async with self._lock:
            self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> Optional[Job]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def mark_running(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = RUNNING

    async def mark_done(self, job_id: str, result: List[Dict[str, Any]]) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = DONE
                job.result = result

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = FAILED
                job.error = error
