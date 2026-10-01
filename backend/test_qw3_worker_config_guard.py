"""
[QW-3] Guard test: cloudbuild.worker.yaml must stay aligned with the real
Redis Cloud Essentials capacity (256 connections).

Bug (Sprint 0 / QW-3): cloudbuild.worker.yaml deployed the worker with
--concurrency=20 and REDIS/CELERY pool overrides of 20, contradicting the
Essentials-tuned code defaults (AGENTS.md §4.4b: rate_limit=6, ai_cache=6,
healthcheck=2, default=3, broker=5, result=5) and risking Redis saturation
when the backend scales.

This test fails if inflated values are re-introduced into the deploy config.
The code defaults in backend/app/core/config.py are the single source of
truth; pool overrides must not live in this YAML (T6 rule).
"""
import re
from pathlib import Path

import yaml

WORKER_YAML = Path(__file__).resolve().parent.parent / "cloudbuild.worker.yaml"

# Essentials-safe upper bounds (AGENTS.md §4.4b). Anything above these in
# the worker deploy config is unsafe for the 256-connection plan.
MAX_SAFE_CONCURRENCY = 4
INFLATED_POOL_VARS = (
    "REDIS_MAX_CONNECTIONS_RATE_LIMIT",
    "REDIS_MAX_CONNECTIONS_AI_CACHE",
    "REDIS_MAX_CONNECTIONS_HEALTHCHECK",
    "REDIS_MAX_CONNECTIONS_DEFAULT",
    "CELERY_BROKER_POOL_LIMIT",
    "CELERY_RESULT_BACKEND_MAX_CONNECTIONS",
)


def _load_deploy_step() -> dict:
    with WORKER_YAML.open() as fh:
        doc = yaml.safe_load(fh)
    steps = {s.get("id"): s for s in doc.get("steps", [])}
    assert "deploy-worker-pool" in steps, "deploy-worker-pool step missing"
    return steps["deploy-worker-pool"]


def test_worker_concurrency_is_essentials_safe():
    args = _load_deploy_step().get("args", [])
    joined = " ".join(str(a) for a in args)
    match = re.search(r"--concurrency=(\d+)", joined)
    assert match, "worker deploy args must pin --concurrency explicitly"
    concurrency = int(match.group(1))
    assert concurrency <= MAX_SAFE_CONCURRENCY, (
        f"--concurrency={concurrency} exceeds Essentials-safe "
        f"{MAX_SAFE_CONCURRENCY} (AGENTS.md §4.4b). Migrate Redis to Pro 1 "
        f"before raising it (§12.3)."
    )


def test_no_inflated_redis_pool_overrides_in_yaml():
    args = _load_deploy_step().get("args", [])
    env_args = [str(a) for a in args if "--update-env-vars" in str(a) or "--env-vars" in str(a)]
    joined = " ".join(env_args)
    for var in INFLATED_POOL_VARS:
        assert var not in joined, (
            f"{var} must not be overridden in cloudbuild.worker.yaml — "
            f"code defaults (backend/app/core/config.py) are the single "
            f"source of truth for the Essentials plan."
        )
