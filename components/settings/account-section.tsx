"use client";

import { useEffect, useState } from "react";
import { Activity, CalendarClock, FileSpreadsheet, ShieldCheck } from "lucide-react";
import { useSupabase } from "@/lib/supabase-provider";
import type { PromDataAuthUser } from "@/components/auth-types";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface AccountSectionProps {
  user: PromDataAuthUser | null;
}

function formatLastSignIn(value: unknown): string {
  if (typeof value !== "string" || !value) return "No disponible";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No disponible";
  return new Intl.DateTimeFormat("es-PE", {
    day: "2-digit",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function AccountSection({ user }: AccountSectionProps) {
  const supabase = useSupabase();
  const [counts, setCounts] = useState<{ files: number | null; reports: number | null }>({
    files: null,
    reports: null,
  });

  const provider = user?.app_metadata?.provider || "email";

  useEffect(() => {
    let isMounted = true;

    const loadCounts = async () => {
      if (!user?.id) return;

      try {
        const [filesResult, reportsResult] = await Promise.all([
          supabase
            .from("uploaded_files")
            .select("*", { count: "exact", head: true })
            .eq("user_id", user.id),
          supabase
            .from("saved_reports")
            .select("*", { count: "exact", head: true })
            .eq("user_id", user.id),
        ]);

        if (!isMounted) return;

        setCounts({
          files: filesResult.error ? null : filesResult.count ?? 0,
          reports: reportsResult.error ? null : reportsResult.count ?? 0,
        });
      } catch (error) {
        console.error("[settings] No se pudo cargar el resumen de actividad:", error);
      }
    };

    void loadCounts();

    return () => {
      isMounted = false;
    };
  }, [supabase, user?.id]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Cuenta conectada</CardTitle>
          <CardDescription>
            Detalles de tu método de acceso y sesión actual.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-3 rounded-lg border border-border/40 px-3 py-2.5">
            <ShieldCheck className="h-4 w-4 shrink-0 text-accent" />
            <span className="text-sm text-muted-foreground">
              Conectado mediante
            </span>
            <span className="ml-auto text-sm font-medium capitalize">
              {provider} OAuth
            </span>
          </div>
          <div className="flex items-center gap-3 rounded-lg border border-border/40 px-3 py-2.5">
            <CalendarClock className="h-4 w-4 shrink-0 text-accent" />
            <span className="text-sm text-muted-foreground">
              Último inicio de sesión
            </span>
            <span className="ml-auto text-right text-sm font-medium">
              {formatLastSignIn(user?.last_sign_in_at)}
            </span>
          </div>
          <div className="flex items-center gap-3 rounded-lg border border-border/40 px-3 py-2.5">
            <Activity className="h-4 w-4 shrink-0 text-accent" />
            <span className="text-sm text-muted-foreground">
              Correo verificado
            </span>
            <span className="ml-auto text-sm font-medium">
              {user?.email_confirmed_at ? "Sí" : "No verificado"}
            </span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Resumen de actividad</CardTitle>
          <CardDescription>
            Datos asociados a tu cuenta en PromData.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-border/40 px-4 py-3">
              <div className="flex items-center gap-2 text-muted-foreground">
                <FileSpreadsheet className="h-4 w-4" />
                <span className="text-xs">Archivos cargados</span>
              </div>
              <p className="mt-1 text-2xl font-semibold tracking-tight">
                {counts.files === null ? "—" : counts.files}
              </p>
            </div>
            <div className="rounded-lg border border-border/40 px-4 py-3">
              <div className="flex items-center gap-2 text-muted-foreground">
                <Activity className="h-4 w-4" />
                <span className="text-xs">Reportes guardados</span>
              </div>
              <p className="mt-1 text-2xl font-semibold tracking-tight">
                {counts.reports === null ? "—" : counts.reports}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
