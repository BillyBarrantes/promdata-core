import type { createClient } from "@/lib/supabase-client";

type SupabaseClient = ReturnType<typeof createClient>;

/**
 * Usuario autenticado derivado del factory real de Supabase.
 * Evita depender de '@supabase/supabase-js' (no es dependencia directa)
 * y elimina el uso de `any` en los componentes de Settings.
 */
export type PromDataAuthUser = NonNullable<
  Awaited<ReturnType<SupabaseClient["auth"]["getUser"]>>["data"]["user"]
>;
