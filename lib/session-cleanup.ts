"use client";

import * as duckdbEngine from "@/lib/duckdb-engine";

export const PROMDATA_STORAGE_PREFIX = "promdata:";
export const CHAT_RECOVERY_STORAGE_KEY = "chat_recovery_context";

export async function clearPromDataBrowserState(): Promise<void> {
  try {
    await duckdbEngine.reset();
  } catch (error) {
    console.error("Error reseteando DuckDB durante cierre de sesión", error);
  }

  if (typeof window === "undefined") {
    return;
  }

  try {
    const keysToRemove: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (!key) continue;
      if (key === CHAT_RECOVERY_STORAGE_KEY || key.startsWith(PROMDATA_STORAGE_PREFIX)) {
        keysToRemove.push(key);
      }
    }
    keysToRemove.forEach((key) => window.localStorage.removeItem(key));
  } catch (error) {
    console.error("Error limpiando localStorage de PromData", error);
  }

  try {
    window.sessionStorage.clear();
  } catch (error) {
    console.error("Error limpiando sessionStorage durante cierre de sesión", error);
  }
}
