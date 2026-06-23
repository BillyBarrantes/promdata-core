"""
Script de estrés MASIVO contra Producción (Vertex AI).
"""

from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass
from time import perf_counter
import httpx

# 1. TU TOKEN REAL
TOKEN_SEGURIDAD = "PEGA_AQUI_TU_TOKEN_REAL"

# 2. TU URL DE PRODUCCIÓN (Cambia esto por la URL real de tu backend en Cloud Run)
# Ejemplo: "https://backend-promdata-xyz.a.run.app/api/v1/analyze"
ENDPOINT_URL = "https://TU_BACKEND_EN_PRODUCCION/api/v1/analyze" 

FILE_ID = "29649149-3962-49eb-a2ab-7cb96e905a9b"
REQUEST_TIMEOUT_SECONDS = 120.0 # Ampliado para soportar el backoff

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

async def send_request(client: httpx.AsyncClient, index: int, base_prompt: str) -> StressResult:
    started_at = perf_counter()
    # TRUCO ROMPE-CACHÉ: Agregamos un hash único al final de cada prompt
    unique_hash = uuid.uuid4().hex[:8]
    prompt_con_hash = f"{base_prompt} [Ignora este hash de prueba: {unique_hash}]"
    
    try:
        response = await client.post(
            ENDPOINT_URL,
            json={"file_id": FILE_ID, "prompt": prompt_con_hash},
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
    if TOKEN_SEGURIDAD == "PEGA_AQUI_TU_TOKEN_REAL":
        print("ERROR: Pega tu token de Supabase en el código.")
        return
    if "TU_BACKEND_EN_PRODUCCION" in ENDPOINT_URL:
        print("ERROR: Coloca la URL real de tu backend en ENDPOINT_URL.")
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