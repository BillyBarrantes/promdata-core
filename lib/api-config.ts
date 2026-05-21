// ---------------------------------------------------------------------------
// [ENTERPRISE v1] Configuración centralizada de la URL base del API
// ---------------------------------------------------------------------------
// Fuente única de verdad para la URL del backend.
// En desarrollo: http://localhost:8000 (default)
// En producción: se lee de NEXT_PUBLIC_API_BASE_URL en .env / .env.local
// ---------------------------------------------------------------------------
export const API_BASE_URL: string =
  (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(
    /\/$/,
    ""
  );
