import json

from app.services import file_cache


def test_file_cache_key_ignores_parent_id_for_same_visible_prompt():
    file_id = "file-1"
    prompt_a = json.dumps({"text": "  La demora   por resolver ", "parent_id": "task-a"})
    prompt_b = json.dumps({"text": "la demora por resolver", "parent_id": "task-b"})

    assert file_cache.build_file_cache_key(file_id, prompt_a) == file_cache.build_file_cache_key(file_id, prompt_b)


def test_parent_context_fingerprint_ignores_volatile_metadata():
    context_a = {
        "parent_task_id": "task-a",
        "result_payload": {
            "traceability": {
                "semantic_context": {
                    "filters": [{"column": "categoria", "operator": "==", "value": "A"}],
                    "updated_at": "2026-06-20T10:00:00Z",
                },
                "plans": [{"id": "volatile-plan", "filters": []}],
            },
            "created_at": "2026-06-20T10:00:00Z",
        },
    }
    context_b = {
        "parent_task_id": "task-b",
        "result_payload": {
            "traceability": {
                "semantic_context": {
                    "filters": [{"column": "categoria", "operator": "==", "value": "A"}],
                    "updated_at": "2026-06-20T11:00:00Z",
                },
                "plans": [{"id": "other-volatile-plan", "filters": []}],
            },
            "created_at": "2026-06-20T11:00:00Z",
        },
    }

    assert file_cache.build_parent_context_fingerprint(context_a) == file_cache.build_parent_context_fingerprint(context_b)


def test_parent_context_fingerprint_changes_with_mathematical_filters():
    context_a = {
        "result_payload": {
            "traceability": {
                "semantic_context": {
                    "filters": [{"column": "categoria", "operator": "==", "value": "A"}],
                },
            },
        },
    }
    context_b = {
        "result_payload": {
            "traceability": {
                "semantic_context": {
                    "filters": [{"column": "categoria", "operator": "==", "value": "B"}],
                },
            },
        },
    }

    assert file_cache.build_parent_context_fingerprint(context_a) != file_cache.build_parent_context_fingerprint(context_b)


def test_file_cache_restores_unscoped_repeat_when_parent_id_changes(monkeypatch):
    monkeypatch.setattr(file_cache, "_get_redis", lambda: None)
    monkeypatch.setattr(file_cache, "_HAS_PARQUET", False)
    with file_cache._MEMORY_LOCK:
        file_cache._MEMORY_CACHE.clear()

    file_id = "file-1"
    first_prompt = json.dumps({"text": "La demora por resolver", "parent_id": None})
    repeat_prompt = json.dumps({"text": "La demora por resolver", "parent_id": "task-2"})
    parent_context = {
        "result_payload": {
            "traceability": {
                "semantic_context": {
                    "filters": [{"column": "estado", "operator": "==", "value": "pendiente"}],
                },
            },
        },
    }
    payload = {"status": "completed", "result": {"analysis": "ok"}}

    file_cache.set_cached_analysis(file_id, first_prompt, payload)
    restored = file_cache.get_cached_analysis(
        file_id,
        repeat_prompt,
        parent_context=parent_context,
        allow_unscoped_fallback=True,
    )

    assert restored == payload
