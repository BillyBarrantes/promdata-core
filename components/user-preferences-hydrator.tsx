"use client";

import { useEffect } from "react";
import { useSetAtom } from "jotai";
import { useSupabase } from "@/lib/supabase-provider";
import {
  isCurrencyCode,
  readStoredPreferences,
  userPreferencesAtom,
  writeStoredPreferences,
  type UserPreferences,
} from "@/components/user-preferences";

/**
 * Hidrata las preferencias de usuario una vez por sesión:
 * 1. localStorage (instantáneo, sin red).
 * 2. `profiles.preferences` de Supabase (durable, gana si difiere).
 *
 * Se monta en `app/layout.tsx` para que cualquier formateador del árbol
 * React tenga acceso al valor correcto.
 */
export function UserPreferencesHydrator() {
  const supabase = useSupabase();
  const setPreferences = useSetAtom(userPreferencesAtom);

  useEffect(() => {
    let isMounted = true;

    setPreferences(readStoredPreferences());

    const hydrateFromServer = async () => {
      try {
        const {
          data: { session },
        } = await supabase.auth.getSession();

        const userId = session?.user?.id;
        if (!userId) return;

        const { data, error } = await supabase
          .from("profiles")
          .select("preferences")
          .eq("id", userId)
          .maybeSingle();

        if (error) throw error;
        if (!isMounted) return;

        const stored = (data?.preferences ?? {}) as Partial<UserPreferences>;
        if (!isCurrencyCode(stored?.currency)) return;

        const next: UserPreferences = { currency: stored.currency };
        writeStoredPreferences(next);
        setPreferences(next);
      } catch (error) {
        console.error("[settings] No se pudieron hidratar las preferencias:", error);
      }
    };

    void hydrateFromServer();

    return () => {
      isMounted = false;
    };
  }, [supabase, setPreferences]);

  return null;
}
