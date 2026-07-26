"""Pydantic request/response models for collector-service's job API."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class JobRequest(BaseModel):
    backend: str
    query: str
    params: Dict[str, Any] = {}


class JobCreatedResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
