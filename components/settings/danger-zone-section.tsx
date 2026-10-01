"use client";

import { useState } from "react";
import { Eraser, LogOut, Trash2, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { useSessionReset, useSignOut } from "@/components/use-session";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function DangerZoneSection() {
  const { clearLocalCache } = useSessionReset();
  const { signOut, isSigningOut } = useSignOut();
  const [isCacheDialogOpen, setIsCacheDialogOpen] = useState(false);
  const [isClearingCache, setIsClearingCache] = useState(false);

  const handleClearCache = async () => {
    setIsClearingCache(true);
    try {
      await clearLocalCache();
      toast.success("Caché local limpiada correctamente.");
      setIsCacheDialogOpen(false);
    } catch (error) {
      console.error("[settings] No se pudo limpiar la caché local:", error);
      toast.error("No se pudo limpiar la caché local.");
    } finally {
      setIsClearingCache(false);
    }
  };

  const handleSignOut = async () => {
    try {
      await signOut();
    } catch {
      toast.error("No se pudo cerrar sesión correctamente.");
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Mantenimiento local</CardTitle>
          <CardDescription>
            Libera memoria del navegador sin afectar tus datos en la nube.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <Eraser className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div>
              <p className="text-sm font-medium">Limpiar caché local</p>
              <p className="text-xs text-muted-foreground">
                Reinicia las tablas temporales de DuckDB y el almacenamiento
                local de PromData.
              </p>
            </div>
          </div>
          <Button
            type="button"
            variant="outline"
            className="shrink-0"
            onClick={() => setIsCacheDialogOpen(true)}
          >
            Limpiar caché
          </Button>
        </CardContent>
      </Card>

      <Card className="border border-destructive/20">
        <CardHeader>
          <div className="flex items-center gap-2">
            <TriangleAlert className="h-4 w-4 text-destructive" />
            <CardTitle className="text-base text-destructive">
              Zona de peligro
            </CardTitle>
          </div>
          <CardDescription>
            Acciones sensibles sobre tu sesión y tu cuenta.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-3 rounded-lg border border-border/40 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Cerrar sesión</p>
              <p className="text-xs text-muted-foreground">
                Finaliza la sesión en este navegador.
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              className="shrink-0"
              onClick={() => void handleSignOut()}
              disabled={isSigningOut}
            >
              <LogOut className="h-4 w-4" />
              {isSigningOut ? "Cerrando..." : "Cerrar sesión"}
            </Button>
          </div>

          <div className="flex flex-col gap-3 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium">Eliminar cuenta</p>
                <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] font-medium text-destructive">
                  Próximamente
                </span>
              </div>
              <p className="text-xs text-muted-foreground">
                La eliminación definitiva requiere confirmación por soporte.
              </p>
            </div>
            <Button type="button" variant="outline" className="shrink-0" disabled>
              <Trash2 className="h-4 w-4" />
              Eliminar
            </Button>
          </div>
        </CardContent>
      </Card>

      <Dialog open={isCacheDialogOpen} onOpenChange={setIsCacheDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Limpiar caché local</DialogTitle>
            <DialogDescription>
              Se reiniciarán las tablas temporales de DuckDB y el almacenamiento
              local de PromData en este navegador. Tus archivos y reportes en la
              nube no se verán afectados.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setIsCacheDialogOpen(false)}
              disabled={isClearingCache}
            >
              Cancelar
            </Button>
            <Button
              type="button"
              onClick={() => void handleClearCache()}
              disabled={isClearingCache}
            >
              {isClearingCache ? "Limpiando..." : "Limpiar caché"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
