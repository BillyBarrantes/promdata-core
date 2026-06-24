"""
Script de estrés E2E - Mide el tiempo de respuesta real de Vertex AI con Polling.
"""
from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass
from time import perf_counter
import httpx

# 1. CAPTURA TU NUEVO TOKEN DE LA WEB Y PÉGALO AQUÍ
TOKEN_SEGURIDAD = "eyJhbGciOiJIUzI1NiIsImtpZCI6IjBiTUpicU12am1rNmRCWG4iLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJodHRwczovL2R4bGtlanNydnVrbnVhamtsdHdtLnN1cGFiYXNlLmNvL2F1dGgvdjEiLCJzdWIiOiIzMGFlYWRhYS00OTc3LTQxNzMtOGM2NS1kZTEyMTQwYmEzNTMiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoxNzgyMzMzNTY0LCJpYXQiOjE3ODIzMjk5NjQsImVtYWlsIjoibGJhcnJhbnRlc2R1QGdtYWlsLmNvbSIsInBob25lIjoiIiwiYXBwX21ldGFkYXRhIjp7InByb3ZpZGVyIjoiZ29vZ2xlIiwicHJvdmlkZXJzIjpbImdvb2dsZSJdfSwidXNlcl9tZXRhZGF0YSI6eyJhdmF0YXJfdXJsIjoiaHR0cHM6Ly9saDMuZ29vZ2xldXNlcmNvbnRlbnQuY29tL2EvQUNnOG9jTEw5ZHNZTFRZMVdLMTdsMV9ub3lqd3E2Q291V1dxWUxqeDZva2FNbkRUemhNd2dRPXM5Ni1jIiwiZW1haWwiOiJsYmFycmFudGVzZHVAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWUsImZ1bGxfbmFtZSI6Ikx1aWxseSBCYXJyYW50ZXMiLCJpc3MiOiJodHRwczovL2FjY291bnRzLmdvb2dsZS5jb20iLCJuYW1lIjoiTHVpbGx5IEJhcnJhbnRlcyIsInBob25lX3ZlcmlmaWVkIjpmYWxzZSwicGljdHVyZSI6Imh0dHBzOi8vbGgzLmdvb2dsZXVzZXJjb250ZW50LmNvbS9hL0FDZzhvY0xMOWRzWUxUWTFXSzE3bDFfbm95andxNkNvdVdXcVlMang2b2thTW5EVHpoTXdnUT1zOTYtYyIsInByb3ZpZGVyX2lkIjoiMTEzNDM1MjE4MzQ5NjUwMDM4MDUzIiwic3ViIjoiMTEzNDM1MjE4MzQ5NjUwMDM4MDUzIn0sInJvbGUiOiJhdXRoZW50aWNhdGVkIiwiYWFsIjoiYWFsMSIsImFtciI6W3sibWV0aG9kIjoib2F1dGgiLCJ0aW1lc3RhbXAiOjE3ODIzMjk5NjR9XSwic2Vzc2lvbl9pZCI6IjM0ZjNmMjRmLWI0NTUtNDNkOS05NjcyLTMzOGQ3NzBjZmEwZCIsImlzX2Fub255bW91cyI6ZmFsc2V9.gPz9kO4mwWC76xt61cjeBRsIgWxrw-uOCYr5d5GSXqY"

BASE_URL = "https://promdata-backend-698138140658.us-east4.run.app/api/v1"
ENDPOINT_ANALYZE = f"{BASE_URL}/analyze"
ENDPOINT_STATUS = f"{BASE_URL}/tasks"
FILE_ID = "29649149-3962-49eb-a2ab-7cb96e905a9b"
REQUEST_TIMEOUT_SECONDS = 180.0

PROMPTS_FINANZAS = [
    "Analiza la evolucion mensual de ingresos y detecta cambios relevantes in la tendencia.",
    "Identifica los principales drivers de variacion in egresos durante todo el periodo disponible.",
    "Compara ingresos contra gastos y explica los meses con mayor brecha financiera.",
    "Genera un analisis de rentabilidad temporal con hallazgos ejecutivos y graficos.",
    "Detecta anomalias financieras significativas y priorizalas por impacto monetario.",
    "Evalua la concentracion de gastos por categoria y muestra los rubros mas relevantes.",
    "Analiza el comportamiento del flujo neto y resume riesgos de liquidez.",
    "Encuentra patrones estacionales in ingresos y egresos con visualizaciones adecuadas.",
    "Calcula los periodos de mayor crecimiento financiero y explica posibles causas.",
    "Revisa la volatilidad de gastos y marca los meses con desviaciones extremas.",
    "Compara el rendimiento financiero por segmento disponible y ordena los mejores resultados.",
    "Explica donde se concentra la mayor perdida o reduccion de margen del dataset.",
    "Genera un dashboard financiero con KPIs, tendencias y recomendaciones actionable.",
    "Analiza la evolucion del margen operativo si existen columnas suficientes para estimarlo.",
    "Identifica relaciones entre categorias financieras y su effecto in el resultado total.",
    "Resume los principales riesgos financieros visibles in los datos historicos.",
    "Construye un ranking de conceptos con mayor impacto in el total de egresos.",
    "Evalua si hay deterioro financiero reciente comparando el ultimo periodo contra el historico.",
    "Detecta oportunidades de optimizacion de costos basadas in los patrones del archivo.",
    "Analiza la estabilidad de ingresos y clasifica meses normales versus atipicos.",
    "Muestra una lectura ejecutiva de salud financiera con metricas clave.",
    "Compara periodos iniciales y finales para identificar mejora o deterioro financiero.",
    "Analiza la dispersion de importes y detecta valores extremos que afecten el resultado.",
    "Genera graficos de tendencia para las variables financieras mas importantes.",
    "Identifica los componentes que explican el mayor porcentaje del movimiento total.",
    "Evalua la consistencia de los datos financieros y senala posibles inconsistencias.",
    "Analiza los cambios acumulados in el tiempo y destaca los puntos de inflexion.",
    "Construye una narrativa financiera para direccion explicando causas, efectos y acciones.",
    "Encuentra correlaciones utiles entre ingresos, costos y cualquier dimension disponible.",
    "Realiza un diagnostico financiero integral con foco in performance, riesgo y eficiencia.",
    "Identifica los tres meses con mejor comportamiento operativo y sus factores clave.",
    "Determina si la velocidad del gasto supera a la velocidad de recaudacion de ingresos.",
    "Genera una matriz de alertas tempranas sobre desviaciones presupuestarias críticas.",
    "Analiza los picos de facturacion y evalua si corresponden a estacionalidad pura.",
    "Muestra el impacto consolidado de las categorias secundarias in el margen global.",
    "Evalua la concentracion de transacciones de alto valor y su riesgo asociado.",
    "Propón una estrategia de reduccion de egresos basada exclusivamente in outliers.",
    "Proyecta una tendencia lineal simple para el proximo trimestre bajo este escenario.",
    "Realiza un desglose de la variacion intermensual mas severa registrada in el archivo.",
    "Resume in cinco puntos clave la salud financiera estructural latente in los datos."
]

@dataclass
class TaskTracker:
    index: int
    task_id: str | None
    status: str
    started_at: float
    ended_at: float | None = None

async def send_request(client: httpx.AsyncClient, index: int, base_prompt: str) -> TaskTracker:
    await asyncio.sleep(index * 2.0) # Escalonamiento humano para Supabase
    unique_hash = uuid.uuid4().hex[:8]
    prompt_con_hash = f"{base_prompt} [Ignora este hash de prueba: {unique_hash}]"
    
    try:
        response = await client.post(ENDPOINT_ANALYZE, json={"file_id": FILE_ID, "prompt": prompt_con_hash})
        if response.status_code == 202:
            task_id = response.json().get("task_id")
            return TaskTracker(index=index, task_id=task_id, status="pending", started_at=perf_counter())
        return TaskTracker(index=index, task_id=None, status=f"api_error_{response.status_code}", started_at=perf_counter())
    except Exception as e:
        return TaskTracker(index=index, task_id=None, status=f"fail_{type(e).__name__}", started_at=perf_counter())

async def check_task_status(client: httpx.AsyncClient, tracker: TaskTracker):
    if tracker.status in ["completed", "failed", "timeout", "rate_limited"]:
        return
    try:
        response = await client.get(f"{ENDPOINT_STATUS}/{tracker.task_id}")
        if response.status_code == 200:
            new_status = response.json().get("status", "pending")
            if new_status in ["completed", "failed", "timeout", "rate_limited"]:
                tracker.status = new_status
                tracker.ended_at = perf_counter()
    except Exception:
        pass # Silencioso en polling para no saturar la pantalla

async def main() -> None:
    if TOKEN_SEGURIDAD == "PEGA_AQUI_TU_NUEVO_TOKEN":
        print("❌ ERROR: Por favor, pega tu nuevo token de Supabase en la variable TOKEN_SEGURIDAD.")
        return

    headers = {"Authorization": f"Bearer {TOKEN_SEGURIDAD}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)

    print(f"🚀 Fase 1: Registrando {len(PROMPTS_FINANZAS)} tareas en el Backend de forma segura...")
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        trackers = await asyncio.gather(*[send_request(client, i + 1, prompt) for i, prompt in enumerate(PROMPTS_FINANZAS)])
    
    active_tasks = [t for t in trackers if t.task_id is not None]
    print(f"✅ Tareas aceptadas por el Backend: {len(active_tasks)}/40.")
    
    if not active_tasks:
        print("❌ Ninguna tarea pudo registrarse. Revisa los logs.")
        return

    print("\n⏳ Fase 2: Entrando en bucle de Polling. Esperando procesamiento de Vertex AI (Gemini)...")
    start_polling_time = perf_counter()
    
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        while any(t.status in ["pending", "processing"] for t in active_tasks):
            await asyncio.sleep(5) # Pregunta cada 5 segundos
            await asyncio.gather(*[check_task_status(client, t) for t in active_tasks])
            
            completed = sum(1 for t in active_tasks if t.status == "completed")
            failed = sum(1 for t in active_tasks if t.status in ["failed", "timeout", "rate_limited"])
            pending = len(active_tasks) - completed - failed
            print(f"   📊 [Progreso] Completadas: {completed} | Fallidas/Filtros: {failed} | En ejecución: {pending}")

    print("\n=== 🏆 RESUMEN DE RESPUESTA REAL DE IA (VERTEX AI) ===")
    total_time = perf_counter() - start_polling_time
    print(f"⏱️ Tiempo total para resolver las 40 tareas: {total_time:.2f} segundos\n")
    
    print("📋 Detalle de espera por Usuario Simulado:")
    for t in active_tasks:
        duration = t.ended_at - t.started_at if t.ended_at else 0
        print(f"   👤 Usuario #{t.index:02d} (Task: {t.task_id[:8]}...) -> Status: {t.status.upper()} | Espera Total: {duration:.2f}s")

if __name__ == "__main__":
    asyncio.run(main())