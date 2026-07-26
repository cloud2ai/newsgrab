"""Pydantic request/response models for playwright-service's HTTP API."""
from typing import Any, Dict, Optional

from pydantic import BaseModel


class ActionRequest(BaseModel):
    action: str
    params: Dict[str, Any] = {}


class ActionResponse(BaseModel):
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    browser_connected: bool
