"use client";

import type { createClient } from "@/lib/supabase-client";

// [Blindaje] '@supabase/supabase-js' no es dependencia directa (pnpm no
// hoista transitivas). El tipo se deriva del factory real, igual que en
// `lib/supabase-provider.tsx`.
type SupabaseClient = ReturnType<typeof createClient>;

/**
 * Alcances válidos para el fallback de QA en desarrollo.
 * Cada scope define el flag de URL que habilita el token sintético.
 */
export type AccessTokenScope = "chat" | "dashboard";

const QA_FALLBACKS: Record<
  AccessTokenScope,
  { flag: string; tokenParam: string }
> = {
  chat: { flag: "__qa_chat", tokenParam: "__qa_chat_token" },
  dashboard: { flag: "__qa_dashboard", tokenParam: "__qa_dashboard_token" },
};

/**
 * Obtiene el access token de Supabase de forma unificada.
 *
 * Reemplaza las 3 implementaciones duplicadas que existían en
 * `chat-interface.tsx`, `dashboard/page.tsx` y `workspace-canvas.tsx`.
 *
 * En desarrollo permite un token sintético vía query params para las
 * suites de QA (`?__qa_chat=1`, `?__qa_dashboard=1`).
 */
export async function getAccessToken(
  supabase: SupabaseClient,
  scope: AccessTokenScope = "chat"
): Promise<string | null> {
  try {
    const {
      data: { session },
    } = await supabase.auth.getSession();

    if (session?.access_token) {
      return session.access_token;
    }
  } catch (error) {
    console.error("[auth-helpers] No se pudo obtener la sesión de Supabase:", error);
    return null;
  }

  if (typeof window !== "undefined" && process.env.NODE_ENV !== "production") {
    const fallback = QA_FALLBACKS[scope];
    if (fallback) {
      const params = new URLSearchParams(window.location.search);
      if (params.get(fallback.flag) === "1") {
        return params.get(fallback.tokenParam) || `qa-${scope}-token`;
      }
    }
  }

  return null;
}
