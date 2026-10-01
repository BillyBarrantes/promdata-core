"use client";

import { useCallback, useState } from "react";
import { atom, useAtom } from "jotai";
import { useSupabase } from "@/lib/supabase-provider";

export type CurrencyCode = "PEN" | "USD" | "EUR";

export interface UserPreferences {
  currency: CurrencyCode;
}

export const DEFAULT_USER_PREFERENCES: UserPreferences = { currency: "PEN" };

export const CURRENCY_LOCALES: Record<CurrencyCode, string> = {
  PEN: "es-PE",
  USD: "en-US",
  EUR: "es-ES",
};

export const CURRENCY_SYMBOLS: Record<CurrencyCode, string> = {
  PEN: "S/",
  USD: "$",
  EUR: "€",
};

export const CURRENCY_OPTIONS: { value: CurrencyCode; label: string }[] = [
  { value: "PEN", label: "Sol peruano (S/)" },
  { value: "USD", label: "Dólar estadounidense ($)" },
  { value: "EUR", label: "Euro (€)" },
];

export const PREFERENCES_STORAGE_KEY = "promdata:user_preferences";

export function isCurrencyCode(value: unknown): value is CurrencyCode {
  return value === "PEN" || value === "USD" || value === "EUR";
}

let cachedPreferences: UserPreferences | null = null;

/**
 * Lectura síncrona de preferencias (cache de módulo + localStorage).
 * Pensada para formateadores no-React (tooltips de ECharts, tablas).
 */
export function readStoredPreferences(): UserPreferences {
  if (cachedPreferences) return cachedPreferences;

  if (typeof window === "undefined") return DEFAULT_USER_PREFERENCES;

  try {
    const raw = window.localStorage.getItem(PREFERENCES_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<UserPreferences>;
      if (isCurrencyCode(parsed?.currency)) {
        cachedPreferences = { currency: parsed.currency };
        return cachedPreferences;
      }
    }
  } catch (error) {
    console.error("[settings] No se pudieron leer las preferencias locales:", error);
  }

  cachedPreferences = DEFAULT_USER_PREFERENCES;
  return cachedPreferences;
}

export function writeStoredPreferences(preferences: UserPreferences): void {
  cachedPreferences = preferences;

  if (typeof window === "undefined") return;

  try {
    window.localStorage.setItem(
      PREFERENCES_STORAGE_KEY,
      JSON.stringify(preferences)
    );
  } catch (error) {
    console.error("[settings] No se pudieron persistir las preferencias locales:", error);
  }
}

export function getActiveCurrency(): CurrencyCode {
  return readStoredPreferences().currency;
}

export const userPreferencesAtom = atom<UserPreferences>(DEFAULT_USER_PREFERENCES);

/**
 * Hook de lectura/escritura de preferencias de usuario.
 * Persiste en tres capas: átomo Jotai (React), localStorage (síncrono) y
 * `profiles.preferences` (Supabase, durable multi-dispositivo).
 */
export function useUserPreferences() {
  const [preferences, setPreferences] = useAtom(userPreferencesAtom);
  const supabase = useSupabase();
  const [isSaving, setIsSaving] = useState(false);

  const persistPreferences = useCallback(
    async (next: UserPreferences): Promise<boolean> => {
      const previous = preferences;
      setPreferences(next);
      writeStoredPreferences(next);
      setIsSaving(true);

      try {
        const {
          data: { user },
        } = await supabase.auth.getUser();

        if (!user) throw new Error("No hay sesión activa.");

        const { error } = await supabase
          .from("profiles")
          .update({ preferences: next })
          .eq("id", user.id);

        if (error) throw error;
        return true;
      } catch (error) {
        console.error("[settings] No se pudieron guardar las preferencias:", error);
        setPreferences(previous);
        writeStoredPreferences(previous);
        return false;
      } finally {
        setIsSaving(false);
      }
    },
    [preferences, setPreferences, supabase]
  );

  const updateCurrency = useCallback(
    (currency: CurrencyCode) => persistPreferences({ ...preferences, currency }),
    [persistPreferences, preferences]
  );

  return { preferences, updateCurrency, isSaving };
}
