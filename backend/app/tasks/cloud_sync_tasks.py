from __future__ import annotations

from app.celery_app import celery_app
from app.core.supabase_client import get_supabase_service_client
from app.services.cloud_imports import materialize_cloud_import
from app.services.cloud_sync_jobs import execute_cloud_sync_job


@celery_app.task(name="perform_cloud_sync_job_task", ignore_result=True)
def perform_cloud_sync_job_task(job_id: str) -> dict[str, str | int | None]:
    service_client = get_supabase_service_client()
    return execute_cloud_sync_job(
        job_id=job_id,
        materialize_import_fn=materialize_cloud_import,
        service_client=service_client,
    )
