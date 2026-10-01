import { API_BASE_URL } from "@/lib/api-config"

type PresentationRecord = Record<string, unknown>

let inFlight: Promise<PresentationRecord[]> | null = null

/**
 * Fetch unificado del listado de presentaciones.
 *
 * Deduplica llamadas concurrentes: si dos consumidores (Dashboard, WorkspaceCanvas,
 * SaveReportDialog) piden la lista al mismo tiempo, se comparte una sola request.
 *
 * Recibe el access token ya resuelto para que cada caller conserve su propio
 * control de sesión (si no hay token, el caller decide no actualizar el estado).
 *
 * No cachea resultados entre llamadas secuenciales para evitar datos stale,
 * ya que los diálogos de guardado actualizan el átomo localmente tras guardar.
 */
export function fetchPresentationsShared(accessToken: string): Promise<PresentationRecord[]> {
  if (inFlight) return inFlight

  inFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/presentations?_t=${Date.now()}`, {
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Cache-Control": "no-cache, no-store, must-revalidate",
        },
        cache: "no-store",
      })

      if (!res.ok) return []
      const data = await res.json().catch(() => [])
      return Array.isArray(data) ? (data as PresentationRecord[]) : []
    } catch {
      return []
    } finally {
      inFlight = null
    }
  })()

  return inFlight
}
