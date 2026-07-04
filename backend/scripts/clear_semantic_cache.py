#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════
clear_semantic_cache.py — Administrador de caché semántico de IA
═══════════════════════════════════════════════════════════════════

Limpia eficientemente el caché de respuestas de IA en Redis
(namespaces: semantic_router, semantic_translator, chart_narrative,
 dashboard_executive_summary, semantic_router_schema).

Usa SCAN (no KEYS) para iterar sin bloquear Redis.
Usa UNLINK (no DEL) para eliminar de forma asíncrona.

Uso:
    python scripts/clear_semantic_cache.py --all          # Todo
    python scripts/clear_semantic_cache.py --translator   # Solo planes
    python scripts/clear_semantic_cache.py --router       # Solo ruteo
    python scripts/clear_semantic_cache.py --all --dry-run # Preview

Seguridad: Solo accede a keys con prefijo 'promdata:ai_cache:',
no afecta rate limits, sesiones, ni otros datos del sistema.
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _BACKEND_DIR)

from app.core.redis_client import get_redis_client
from app.core.structured_logging import emit_structured_log

_CACHE_PREFIX = "promdata:ai_cache"
_SCHEMA_VERSION = "v3"
_BATCH_SIZE = 100

_SEMANTIC_NAMESPACES: dict[str, str] = {
    "router": "semantic_router",
    "translator": "semantic_translator",
    "narrative": "chart_narrative",
    "summary": "dashboard_executive_summary",
    "schema": "semantic_router_schema",
}

_ALL_NAMESPACES = tuple(_SEMANTIC_NAMESPACES.values())


def _clear_namespace(
    client: Any,
    namespace: str,
    *,
    dry_run: bool = False,
) -> int:
    """Limpia todas las keys de un namespace usando SCAN + UNLINK."""
    pattern = f"{_CACHE_PREFIX}:{namespace}:*"
    cursor = 0
    deleted = 0

    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=_BATCH_SIZE)
        if keys:
            deleted += len(keys)
            if not dry_run:
                client.unlink(*keys)
            print(f"  📦 {namespace}: {len(keys)} keys {'(dry-run)' if dry_run else 'eliminadas'}")
        if cursor == 0:
            break

    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Limpia el caché semántico de IA en Redis (SCAN + UNLINK).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ejemplos:\n"
        "  python scripts/clear_semantic_cache.py --translator\n"
        "  python scripts/clear_semantic_cache.py --all --dry-run\n"
        "  python scripts/clear_semantic_cache.py --router --translator",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Limpia TODOS los namespaces semánticos",
    )
    parser.add_argument(
        "--router",
        action="store_true",
        help="Limpia decisiones de ruteo (semantic_router)",
    )
    parser.add_argument(
        "--translator",
        action="store_true",
        help="Limpia planes analíticos (semantic_translator)",
    )
    parser.add_argument(
        "--narrative",
        action="store_true",
        help="Limpia narrativas de charts (chart_narrative)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Limpia resúmenes ejecutivos (dashboard_executive_summary)",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Limpia fingerprints de schema (semantic_router_schema)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo muestra qué se eliminaría, sin eliminarlo realmente",
    )

    args = parser.parse_args()

    namespaces: list[str] = []
    if args.all:
        namespaces = list(_ALL_NAMESPACES)
    else:
        for flag, ns in _SEMANTIC_NAMESPACES.items():
            if getattr(args, flag):
                namespaces.append(ns)

    if not namespaces:
        parser.print_help()
        print("\n⚠️  Debes especificar al menos una opción de limpieza.")
        sys.exit(1)

    # Conectar a Redis
    redis_client = get_redis_client(purpose="ai_response_cache")
    if redis_client is None:
        print("❌ No se pudo conectar a Redis. Verifica REDIS_URL y la conexión.")
        sys.exit(1)

    print(f"\n{'=' * 50}")
    print(f"🧹 Limpieza de caché semántico de IA")
    print(f"{'=' * 50}")
    print(f"Prefijo: {_CACHE_PREFIX}:{_SCHEMA_VERSION}")
    print(f"Namespaces a limpiar: {namespaces}")
    print(f"Modo: {'DRY-RUN (sin cambios)' if args.dry_run else 'APLICACIÓN REAL'}")
    print(f"{'=' * 50}\n")

    start_time = time.monotonic()
    total_deleted = 0

    try:
        for namespace in namespaces:
            print(f"🔍 Escaneando {namespace}...")
            deleted = _clear_namespace(redis_client, namespace, dry_run=args.dry_run)
            total_deleted += deleted

        elapsed = round(time.monotonic() - start_time, 2)
        print(f"\n{'=' * 50}")
        if args.dry_run:
            print(f"🔍 DRY-RUN: {total_deleted} keys serían eliminadas en {elapsed}s")
        else:
            print(f"✅ {total_deleted} keys eliminadas en {elapsed}s")
        print(f"{'=' * 50}")

        emit_structured_log(
            "semantic_cache_cleared",
            namespaces=namespaces,
            deleted_keys=total_deleted,
            dry_run=args.dry_run,
            elapsed_seconds=elapsed,
        )

    except Exception as exc:
        print(f"❌ Error durante la limpieza: {exc}")
        emit_structured_log(
            "semantic_cache_clear_error",
            level="error",
            namespaces=namespaces,
            error=str(exc)[:300],
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
