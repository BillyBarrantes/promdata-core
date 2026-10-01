"use client";

import { useCallback, useState } from "react";
import { useSetAtom } from "jotai";
import { useSupabase } from "@/lib/supabase-provider";
import { clearPromDataBrowserState } from "@/lib/session-cleanup";
import {
  crossFilterAtom,
  drillDownAtom,
  duckdbReadyAtom,
  filtersAtom,
  presentationStateAtom,
  workspaceItemsAtom,
  workspaceRenderStateAtom,
} from "@/lib/state";

/**
 * Reseteo de sesión de PromData. Reutilizado por:
 * - Sidebar (cerrar sesión).
 * - `/settings` > Zona de Peligro (cerrar sesión y limpiar caché local).
 *
 * Mantiene una única ruta de limpieza para evitar divergencias (§ Módulo 9).
 */
export function useSessionReset() {
  const setFilters = useSetAtom(filtersAtom);
  const setCrossFilters = useSetAtom(crossFilterAtom);
  const setDuckdbReady = useSetAtom(duckdbReadyAtom);
  const setWorkspaceItems = useSetAtom(workspaceItemsAtom);
  const setWorkspaceRenderState = useSetAtom(workspaceRenderStateAtom);
  const setPresentationState = useSetAtom(presentationStateAtom);
  const setDrillDown = useSetAtom(drillDownAtom);

  const resetSessionState = useCallback(() => {
    setFilters({});
    setCrossFilters({});
    setWorkspaceItems([]);
    setDuckdbReady(false);
    setPresentationState({
      activePresentationId: null,
      activeFileId: null,
      globalFilters: {},
      presentations: [],
      widgets: [],
    });
    setWorkspaceRenderState({
      status: "idle",
      message: null,
      pendingVisuals: 0,
      renderedVisuals: 0,
    });
    setDrillDown({
      isVisible: false,
      position: { x: 0, y: 0 },
      dataContext: { category: "", value: 0, series: "" },
    });
  }, [
    setFilters,
    setCrossFilters,
    setDuckdbReady,
    setWorkspaceItems,
    setWorkspaceRenderState,
    setPresentationState,
    setDrillDown,
  ]);

  const clearLocalCache = useCallback(async () => {
    resetSessionState();
    await clearPromDataBrowserState();
  }, [resetSessionState]);

  return { resetSessionState, clearLocalCache };
}

/**
 * Cierre de sesión completo:
 * 1. Resetea los átomos de Jotai.
 * 2. Limpia el estado del navegador (DuckDB-WASM, localStorage `promdata:`, sessionStorage).
 * 3. Cierra la sesión en Supabase y redirige a `/login`.
 */
export function useSignOut() {
  const supabase = useSupabase();
  const { resetSessionState } = useSessionReset();
  const [isSigningOut, setIsSigningOut] = useState(false);

  const signOut = useCallback(async () => {
    setIsSigningOut(true);

    try {
      resetSessionState();
      await clearPromDataBrowserState();
      await supabase.auth.signOut();
      window.location.replace("/login");
    } catch (error) {
      console.error("Error cerrando sesión", error);
      setIsSigningOut(false);
      throw error;
    }
  }, [supabase, resetSessionState]);

  return { signOut, isSigningOut };
}
