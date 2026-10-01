"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Check, Loader2, Monitor, Moon, Sun } from "lucide-react";
import { toast } from "sonner";
import {
  CURRENCY_OPTIONS,
  useUserPreferences,
  type CurrencyCode,
} from "@/components/user-preferences";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

const THEME_OPTIONS = [
  { value: "light", label: "Claro", icon: Sun },
  { value: "dark", label: "Oscuro", icon: Moon },
  { value: "system", label: "Sistema", icon: Monitor },
] as const;

export function AppearanceSection() {
  const { theme, setTheme } = useTheme();
  const { preferences, updateCurrency, isSaving } = useUserPreferences();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const handleCurrencyChange = async (currency: CurrencyCode) => {
    if (currency === preferences.currency || isSaving) return;

    const ok = await updateCurrency(currency);
    if (ok) {
      toast.success("Moneda actualizada correctamente.");
    } else {
      toast.error("No se pudo guardar la moneda. Inténtalo de nuevo.");
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Tema de la interfaz</CardTitle>
          <CardDescription>
            Elige cómo se ve PromData en este dispositivo.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 sm:grid-cols-3">
            {THEME_OPTIONS.map((option) => {
              const Icon = option.icon;
              const isActive = mounted && theme === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setTheme(option.value)}
                  disabled={!mounted}
                  className={cn(
                    "flex items-center gap-2 rounded-lg border border-border/50 px-3 py-2.5 text-sm transition-colors duration-200 ease-[cubic-bezier(0.2,0,0,1)] disabled:opacity-60",
                    isActive
                      ? "border-accent/60 bg-accent/5 text-foreground"
                      : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span>{option.label}</span>
                  {isActive && <Check className="ml-auto h-4 w-4 text-accent" />}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Moneda y formato</CardTitle>
          <CardDescription>
            Se usa para formatear los valores monetarios de tus KPIs y reportes.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {CURRENCY_OPTIONS.map((option) => {
            const isActive = preferences.currency === option.value;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => void handleCurrencyChange(option.value)}
                disabled={isSaving}
                className={cn(
                  "flex w-full items-center justify-between gap-3 rounded-lg border border-border/50 px-3 py-2.5 text-left text-sm transition-colors duration-200 ease-[cubic-bezier(0.2,0,0,1)] disabled:opacity-60",
                  isActive
                    ? "border-accent/60 bg-accent/5 text-foreground"
                    : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
                )}
              >
                <span>{option.label}</span>
                {isActive &&
                  (isSaving ? (
                    <Loader2 className="h-4 w-4 animate-spin text-accent" />
                  ) : (
                    <Check className="h-4 w-4 text-accent" />
                  ))}
              </button>
            );
          })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Idioma</CardTitle>
          <CardDescription>Idioma de la interfaz de PromData.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between gap-4 rounded-lg border border-border/40 px-3 py-2.5">
            <span className="text-sm">Español</span>
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              Próximamente
            </span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
