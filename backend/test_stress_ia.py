"""
Script descartable para prueba empírica de RPM contra Vertex AI.

Uso:
1. Pega un JWT válido en TOKEN_SEGURIDAD.
2. Asegura que FastAPI local esté corriendo en http://127.0.0.1:8000.
3. Ejecuta: ./venv/bin/python test_stress_ia.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter

import httpx


TOKEN_SEGURIDAD = "eyJhbGciOiJIUzI1NiIsImtpZCI6IjBiTUpicU12am1rNmRCWG4iLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJodHRwczovL2R4bGtlanNydnVrbnVhamtsdHdtLnN1cGFiYXNlLmNvL2F1dGgvdjEiLCJzdWIiOiIzMGFlYWRhYS00OTc3LTQxNzMtOGM2NS1kZTEyMTQwYmEzNTMiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoxNzgxOTE0NDg2LCJpYXQiOjE3ODE5MTA4ODYsImVtYWlsIjoibGJhcnJhbnRlc2R1QGdtYWlsLmNvbSIsInBob25lIjoiIiwiYXBwX21ldGFkYXRhIjp7InByb3ZpZGVyIjoiZ29vZ2xlIiwicHJvdmlkZXJzIjpbImdvb2dsZSJdfSwidXNlcl9tZXRhZGF0YSI6eyJhdmF0YXJfdXJsIjoiaHR0cHM6Ly9saDMuZ29vZ2xldXNlcmNvbnRlbnQuY29tL2EvQUNnOG9jTEw5ZHNZTFRZMVdLMTdsMV9ub3lqd3E2Q291V1dxWUxqeDZva2FNbkRUemhNd2dRPXM5Ni1jIiwiZW1haWwiOiJsYmFycmFudGVzZHVAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWUsImZ1bGxfbmFtZSI6Ikx1aWxseSBCYXJyYW50ZXMiLCJpc3MiOiJodHRwczovL2FjY291bnRzLmdvb2dsZS5jb20iLCJuYW1lIjoiTHVpbGx5IEJhcnJhbnRlcyIsInBob25lX3ZlcmlmaWVkIjpmYWxzZSwicGljdHVyZSI6Imh0dHBzOi8vbGgzLmdvb2dsZXVzZXJjb250ZW50LmNvbS9hL0FDZzhvY0xMOWRzWUxUWTFXSzE3bDFfbm95andxNkNvdVdXcVlMang2b2thTW5EVHpoTXdnUT1zOTYtYyIsInByb3ZpZGVyX2lkIjoiMTEzNDM1MjE4MzQ5NjUwMDM4MDUzIiwic3ViIjoiMTEzNDM1MjE4MzQ5NjUwMDM4MDUzIn0sInJvbGUiOiJhdXRoZW50aWNhdGVkIiwiYWFsIjoiYWFsMSIsImFtciI6W3sibWV0aG9kIjoib2F1dGgiLCJ0aW1lc3RhbXAiOjE3ODE5MTA4ODZ9XSwic2Vzc2lvbl9pZCI6ImU2MDAxY2YxLWMxZGYtNGMyMC05YTVlLWE5ZjE4ZTcyYTZiNSIsImlzX2Fub255bW91cyI6ZmFsc2V9.X82krp9pPqeWsX8zfNJc9d0RwMo1JJ05CPvv09STvyw"
ENDPOINT_URL = "http://127.0.0.1:8000/api/v1/analyze"
FILE_ID = "29649149-3962-49eb-a2ab-7cb96e905a9b"
REQUEST_TIMEOUT_SECONDS = 90.0


PROMPTS_FINANZAS = [
    "Analiza la evolucion mensual de ingresos y detecta cambios relevantes en la tendencia.",
    "Identifica los principales drivers de variacion en egresos durante todo el periodo disponible.",
    "Compara ingresos contra gastos y explica los meses con mayor brecha financiera.",
    "Genera un analisis de rentabilidad temporal con hallazgos ejecutivos y graficos.",
    "Detecta anomalias financieras significativas y priorizalas por impacto monetario.",
    "Evalua la concentracion de gastos por categoria y muestra los rubros mas relevantes.",
    "Analiza el comportamiento del flujo neto y resume riesgos de liquidez.",
    "Encuentra patrones estacionales en ingresos y egresos con visualizaciones adecuadas.",
    "Calcula los periodos de mayor crecimiento financiero y explica posibles causas.",
    "Revisa la volatilidad de gastos y marca los meses con desviaciones extremas.",
    "Compara el rendimiento financiero por segmento disponible y ordena los mejores resultados.",
    "Explica donde se concentra la mayor perdida o reduccion de margen del dataset.",
    "Genera un dashboard financiero con KPIs, tendencias y recomendaciones accionables.",
    "Analiza la evolucion del margen operativo si existen columnas suficientes para estimarlo.",
    "Identifica relaciones entre categorias financieras y su efecto en el resultado total.",
    "Resume los principales riesgos financieros visibles en los datos historicos.",
    "Construye un ranking de conceptos con mayor impacto en el total de egresos.",
    "Evalua si hay deterioro financiero reciente comparando el ultimo periodo contra el historico.",
    "Detecta oportunidades de optimizacion de costos basadas en los patrones del archivo.",
    "Analiza la estabilidad de ingresos y clasifica meses normales versus atipicos.",
    "Muestra una lectura ejecutiva de salud financiera con metricas clave.",
    "Compara periodos iniciales y finales para identificar mejora o deterioro financiero.",
    "Analiza la dispersion de importes y detecta valores extremos que afecten el resultado.",
    "Genera graficos de tendencia para las variables financieras mas importantes.",
    "Identifica los componentes que explican el mayor porcentaje del movimiento total.",
    "Evalua la consistencia de los datos financieros y senala posibles inconsistencias.",
    "Analiza los cambios acumulados en el tiempo y destaca los puntos de inflexion.",
    "Construye una narrativa financiera para direccion explicando causas, efectos y acciones.",
    "Encuentra correlaciones utiles entre ingresos, costos y cualquier dimension disponible.",
    "Realiza un diagnostico financiero integral con foco en performance, riesgo y eficiencia.",
]


@dataclass
class StressResult:
    index: int
    status_code: int | None
    ok: bool
    elapsed_ms: int
    body_preview: str


async def send_request(client: httpx.AsyncClient, index: int, prompt: str) -> StressResult:
    started_at = perf_counter()
    try:
        response = await client.post(
            ENDPOINT_URL,
            json={"file_id": FILE_ID, "prompt": prompt},
        )
        elapsed_ms = int((perf_counter() - started_at) * 1000)
        return StressResult(
            index=index,
            status_code=response.status_code,
            ok=200 <= response.status_code < 300,
            elapsed_ms=elapsed_ms,
            body_preview=response.text[:220].replace("\n", " "),
        )
    except Exception as exc:
        elapsed_ms = int((perf_counter() - started_at) * 1000)
        return StressResult(
            index=index,
            status_code=None,
            ok=False,
            elapsed_ms=elapsed_ms,
            body_preview=f"{type(exc).__name__}: {exc}",
        )


async def main() -> None:
    if TOKEN_SEGURIDAD == "PEGA_AQUI_TU_TOKEN":
        print("ERROR: pega un token valido en TOKEN_SEGURIDAD antes de ejecutar la prueba.")
        return

    headers = {
        "Authorization": f"Bearer {TOKEN_SEGURIDAD}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    started_at = perf_counter()

    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        results = await asyncio.gather(
            *[
                send_request(client, index + 1, prompt)
                for index, prompt in enumerate(PROMPTS_FINANZAS)
            ]
        )

    total_elapsed_ms = int((perf_counter() - started_at) * 1000)
    successes = [result for result in results if result.ok]
    failures = [result for result in results if not result.ok]

    print("\n=== RESUMEN STRESS IA ===")
    print(f"Endpoint: {ENDPOINT_URL}")
    print(f"File ID: {FILE_ID}")
    print(f"Total enviadas: {len(results)}")
    print(f"Exitos HTTP 2xx: {len(successes)}")
    print(f"Rebotadas/fallidas: {len(failures)}")
    print(f"Duracion total: {total_elapsed_ms} ms")

    print("\n=== DETALLE ===")
    for result in results:
        status = result.status_code if result.status_code is not None else "ERROR"
        marker = "OK" if result.ok else "FAIL"
        print(
            f"[{marker}] #{result.index:02d} status={status} "
            f"elapsed={result.elapsed_ms}ms body={result.body_preview}"
        )


if __name__ == "__main__":
    asyncio.run(main())
