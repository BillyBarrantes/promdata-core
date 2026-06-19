"""
FASE 1 — Día 1: contrato de colas de prioridad Celery.

Valida que las tareas pesadas y de background quedan aisladas por cola sin
cambiar la lógica de ejecución de los wrappers.
"""

from app.celery_app import celery_app
from app.core.config import settings


def test_celery_declares_priority_queues():
    queue_names = {queue.name for queue in celery_app.conf.task_queues}

    assert settings.CELERY_QUEUE_DEFAULT in queue_names
    assert settings.CELERY_QUEUE_ANALYSIS in queue_names
    assert settings.CELERY_QUEUE_BACKGROUND in queue_names
    assert len(queue_names) == 3


def test_celery_routes_heavy_and_background_tasks_to_separate_queues():
    routes = celery_app.conf.task_routes

    assert routes["perform_analysis_task"]["queue"] == settings.CELERY_QUEUE_ANALYSIS
    assert routes["perform_analysis_task_universal_tabular"]["queue"] == settings.CELERY_QUEUE_ANALYSIS
    assert routes["observe_canonical_shadow_runtime"]["queue"] == settings.CELERY_QUEUE_BACKGROUND
    assert routes["observe_canonical_tabular_canary_runtime"]["queue"] == settings.CELERY_QUEUE_BACKGROUND
    assert routes["perform_cloud_sync_job_task"]["queue"] == settings.CELERY_QUEUE_BACKGROUND
    assert routes["process_knowledge_document_task"]["queue"] == settings.CELERY_QUEUE_DEFAULT


def test_celery_default_queue_is_explicit():
    assert celery_app.conf.task_default_queue == settings.CELERY_QUEUE_DEFAULT


def test_analysis_tasks_use_expected_time_limits():
    legacy_task = celery_app.tasks["perform_analysis_task"]
    universal_task = celery_app.tasks["perform_analysis_task_universal_tabular"]

    assert legacy_task.soft_time_limit == settings.CELERY_TASK_HEAVY_SOFT_TIME_LIMIT
    assert legacy_task.time_limit == settings.CELERY_TASK_HEAVY_HARD_TIME_LIMIT
    assert universal_task.soft_time_limit == settings.CELERY_TASK_SOFT_TIME_LIMIT
    assert universal_task.time_limit == settings.CELERY_TASK_HARD_TIME_LIMIT
