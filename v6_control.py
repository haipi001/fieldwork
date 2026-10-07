"""Read-only V6 compatibility endpoints over the existing runtime state."""
from __future__ import annotations

from fastapi import APIRouter, Query

import final_core
from v6_runtime_bridge import list_events, list_model_call_snapshots, list_runners

router = APIRouter(prefix="/api/v1/v6", tags=["V6 Control Plane"])


@router.get("/campaigns/{campaign_id}/runtime-events")
def campaign_runtime_events(campaign_id: str, after_id: int = Query(0, ge=0),
                            limit: int = Query(100, ge=1, le=500)):
    with final_core.connect() as db:
        return {"events": list_events(db, after_id=after_id, limit=limit, campaign_id=campaign_id)}


@router.get("/tasks/{task_id}/model-call-snapshots")
def task_model_call_snapshots(task_id: str):
    with final_core.connect() as db:
        return {"calls": list_model_call_snapshots(db, task_id=task_id)}


@router.get("/runners")
def runners():
    with final_core.connect() as db:
        return {"runners": list_runners(db)}
