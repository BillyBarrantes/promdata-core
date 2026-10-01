"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useSupabase } from "@/lib/supabase-provider";
import { resolveDisplayName, resolveInitials } from "@/components/user-display";
import type { PromDataAuthUser } from "@/components/auth-types";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface ProfileSectionProps {
  user: PromDataAuthUser | null;
  isLoading: boolean;
  onUserUpdated?: (user: PromDataAuthUser) => void;
}

export function ProfileSection({
  user,
  isLoading,
  onUserUpdated,
}: ProfileSectionProps) {
  const supabase = useSupabase();
  const [fullName, setFullName] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isCopied, setIsCopied] = useState(false);

  useEffect(() => {
    if (user) setFullName(resolveDisplayName(user));
  }, [user]);

  const initials = useMemo(
    () => resolveInitials(fullName || resolveDisplayName(user)),
    [fullName, user]
  );

  const email = user?.email || "Sin correo visible";
  const userId = user?.id || "No disponible";
  const provider = user?.app_metadata?.provider || "email";
  const hasChanges =
    !isLoading && user != null && fullName.trim() !== resolveDisplayName(user);

  const handleSave = async () => {
    const trimmed = fullName.trim();
    if (!trimmed) {
      toast.error("El nombre no puede estar vacío.");
      return;
    }

    setIsSaving(true);
    try {
      const { data, error } = await supabase.auth.updateUser({
        data: { full_name: trimmed },
      });
      if (error) throw error;

      toast.success("Nombre actualizado correctamente.");
      if (data?.user) onUserUpdated?.(data.user);
    } catch (error) {
      console.error("[settings] No se pudo actualizar el nombre:", error);
      toast.error("No se pudo actualizar el nombre. Inténtalo de nuevo.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCopyId = async () => {
    if (!user?.id) return;
    try {
      await navigator.clipboard.writeText(user.id);
      setIsCopied(true);
      toast.success("Identificador copiado.");
      window.setTimeout(() => setIsCopied(false), 1800);
    } catch (error) {
      console.error("[settings] No se pudo copiar el ID:", error);
      toast.error("No se pudo copiar el identificador.");
    }
  };

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <div className="h-5 w-32 animate-pulse rounded-md bg-muted/60" />
          <div className="h-4 w-56 animate-pulse rounded-md bg-muted/40" />
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="h-20 animate-pulse rounded-xl bg-muted/30" />
          <div className="h-10 animate-pulse rounded-md bg-muted/30" />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Información del perfil</CardTitle>
          <CardDescription>
            Tu nombre se usa en el saludo de Inicio y en tu avatar.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex items-center gap-4 rounded-xl border border-border/40 bg-muted/20 p-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground">
              <span className="text-lg font-medium">{initials}</span>
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">
                {resolveDisplayName(user)}
              </p>
              <p className="truncate text-xs text-muted-foreground">{email}</p>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="profile-full-name">Nombre para mostrar</Label>
            <Input
              id="profile-full-name"
              value={fullName}
              onChange={(event) => setFullName(event.target.value)}
              placeholder="Tu nombre"
              maxLength={80}
              disabled={isSaving}
            />
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-border/20 pt-4">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setFullName(resolveDisplayName(user))}
              disabled={!hasChanges || isSaving}
            >
              Descartar
            </Button>
            <Button
              type="button"
              onClick={() => void handleSave()}
              disabled={!hasChanges || isSaving}
            >
              {isSaving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Check className="h-4 w-4" />
              )}
              Guardar cambios
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Datos de la cuenta</CardTitle>
          <CardDescription>
            Estos valores provienen de tu proveedor de autenticación.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between gap-4 rounded-lg border border-border/40 px-3 py-2.5">
            <span className="text-sm text-muted-foreground">Correo</span>
            <span className="truncate text-sm font-medium">{email}</span>
          </div>
          <div className="flex items-center justify-between gap-4 rounded-lg border border-border/40 px-3 py-2.5">
            <span className="text-sm text-muted-foreground">Proveedor</span>
            <span className="text-sm font-medium capitalize">{provider}</span>
          </div>
          <div className="flex items-center justify-between gap-4 rounded-lg border border-border/40 px-3 py-2.5">
            <span className="shrink-0 text-sm text-muted-foreground">
              Identificador
            </span>
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate font-mono text-xs">{userId}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                onClick={() => void handleCopyId()}
                disabled={!user?.id}
                aria-label="Copiar identificador"
              >
                {isCopied ? (
                  <Check className="h-3.5 w-3.5 text-accent" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
