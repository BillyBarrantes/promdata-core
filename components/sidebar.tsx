"use client"

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Home, PieChart, Database, BookOpen, Library, LogOut, LogIn, Settings, Sun, Moon } from "lucide-react";
import { CollapseIcon } from "./icons/collapse-icon";
import { atom, useAtom } from "jotai";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { useSupabase } from "@/lib/supabase-provider";
import { toast } from "sonner";
import { useSignOut } from "@/components/use-session";
import { resolveDisplayName, resolveInitials } from "@/components/user-display";

export const sidebarStateAtom = atom(false);

const sidebarItems = [
  { id: "inicio", label: "Inicio", path: "/", icon: <Home className="h-[18px] w-[18px]" /> },
  { id: "dashboard", label: "Dashboard", path: "/dashboard", icon: <PieChart className="h-[18px] w-[18px]" /> },
  { id: "cargar-datos", label: "Cargar Datos", path: "/cargar-datos", icon: <Database className="h-[18px] w-[18px]" /> },
  { id: "conocimiento", label: "Conocimiento", path: "/conocimiento", icon: <Library className="h-[18px] w-[18px]" /> },
  { id: "glosario", label: "Glosario", path: "/glosario", icon: <BookOpen className="h-[18px] w-[18px]" /> },
  { id: "settings", label: "Configuración", path: "/settings", icon: <Settings className="h-[18px] w-[18px]" /> },
];

export function Sidebar() {
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();
  const [isCollapsed, setIsCollapsed] = useAtom(sidebarStateAtom);
  const supabase = useSupabase();
  const { signOut, isSigningOut } = useSignOut();
  const [user, setUser] = useState<any | null>(null);
  const [isMenuOpen, setIsMenuOpen] = useState(false);

  useEffect(() => {
    let isMounted = true;

    const hydrateUser = async () => {
      const { data: { user: currentUser } } = await supabase.auth.getUser();
      if (isMounted) {
        setUser(currentUser ?? null);
      }
    };

    void hydrateUser();

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event: string, session: any) => {
      if (!isMounted) return;
      setUser(session?.user ?? null);
    });

    return () => {
      isMounted = false;
      subscription.unsubscribe();
    };
  }, [supabase]);

  const profileName = useMemo(() => resolveDisplayName(user), [user]);

  const profileEmail = useMemo(() => {
    return typeof user?.email === "string" && user.email.trim() ? user.email.trim() : "Sin correo visible";
  }, [user]);

  const profileInitials = useMemo(() => resolveInitials(profileName), [profileName]);

  const handleSignOut = async () => {
    if (isSigningOut) return;

    setIsMenuOpen(false);
    try {
      await signOut();
    } catch {
      toast.error("No se pudo cerrar sesión correctamente.");
    }
  };

  const setMenuOpenSafely = (nextOpen: boolean) => {
    if (isSigningOut) return;
    setIsMenuOpen(nextOpen);
  };

  return (
    <div className={cn(
      "bg-sidebar border-r border-sidebar-border/60 flex h-screen min-h-screen shrink-0 flex-col transition-[width] duration-[350ms] ease-[cubic-bezier(0.2,0,0,1)]",
      isCollapsed ? "w-20" : "w-56"
    )}>
      <div className="p-4 border-b border-sidebar-border/40">
        <div className={cn("flex items-center gap-2.5", isCollapsed && "justify-center")}>
          <div className="w-8 h-8 bg-accent rounded-lg shadow-sm flex items-center justify-center flex-shrink-0 transition-transform duration-200 ease-[cubic-bezier(0.2,0,0,1)] hover:scale-105">
            <span className="text-accent-foreground font-bold text-sm">P</span>
          </div>
          {!isCollapsed && (
            <span className="font-semibold text-sidebar-foreground tracking-tight transition-opacity duration-200 ease-[cubic-bezier(0.2,0,0,1)]">
              PromData
            </span>
          )}
        </div>
      </div>

      {/* Navegación Principal con Tooltips */}
      <nav className="flex-1 p-2">
        <TooltipProvider delayDuration={0}>
          <ul className="space-y-1">
            {sidebarItems.map((item) => (
              <li key={item.id}>
                {item.id === "settings" && (
                  <div className="px-1 pb-1.5 pt-2">
                    <Separator className="bg-sidebar-border/50" />
                  </div>
                )}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      asChild
                      variant="ghost"
                      className={cn(
                        "relative w-full justify-start gap-2.5 text-sidebar-foreground font-normal hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)] rounded-lg h-9",
                        pathname === item.path && "bg-sidebar-accent text-sidebar-accent-foreground font-medium shadow-[var(--cursor-shadow-sm)]",
                        isCollapsed && "justify-center"
                      )}
                      data-testid={`sidebar-nav-${item.id}`}
                    >
                      <Link href={item.path} prefetch>
                        {pathname === item.path && (
                          <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-full bg-accent shadow-[0_0_8px_oklch(0.72_0.14_28/0.4)] transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)]" />
                        )}
                        {item.icon}
                        {!isCollapsed && <span className="transition-opacity duration-200 ease-[cubic-bezier(0.2,0,0,1)]">{item.label}</span>}
                      </Link>
                    </Button>
                  </TooltipTrigger>
                  {isCollapsed && (
                    <TooltipContent side="right">
                      <p>{item.label}</p>
                    </TooltipContent>
                  )}
                </Tooltip>
              </li>
            ))}
          </ul>
        </TooltipProvider>
      </nav>

      <div className="mt-auto flex flex-col">
        {/* 2. Botones inferiores unificados sin recuadros */}
        <nav className="p-2 border-t border-sidebar-border/40">
          <TooltipProvider delayDuration={0}>
            <ul className="space-y-1">
              <li>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      className={cn("w-full justify-start gap-2 text-sidebar-foreground font-light hover:bg-sidebar-accent/60 transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)] rounded-lg h-9", isCollapsed && "justify-center")}
                      onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                    >
                      <span className="relative inline-flex items-center justify-center h-4 w-4">
                        <Sun
                          className={cn(
                            "absolute h-4 w-4 transition-all duration-500 ease-[cubic-bezier(0.2,0,0,1)]",
                            theme === 'dark'
                              ? "opacity-100 rotate-0 scale-100 text-amber-400"
                              : "opacity-0 -rotate-90 scale-50"
                          )}
                        />
                        <Moon
                          className={cn(
                            "absolute h-4 w-4 transition-all duration-500 ease-[cubic-bezier(0.2,0,0,1)]",
                            theme === 'dark'
                              ? "opacity-0 rotate-90 scale-50"
                              : "opacity-100 rotate-0 scale-100"
                          )}
                        />
                      </span>
                      {!isCollapsed && <span className="transition-opacity duration-200 ease-[cubic-bezier(0.2,0,0,1)]">{theme === 'dark' ? 'Modo claro' : 'Modo oscuro'}</span>}
                    </Button>
                  </TooltipTrigger>
                  {isCollapsed && (
                    <TooltipContent side="right">
                      <p>Modo oscuro</p>
                    </TooltipContent>
                  )}
                </Tooltip>
              </li>
              <li>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      className={cn("w-full justify-start gap-2 text-sidebar-foreground font-light hover:bg-sidebar-accent/60 transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)] rounded-lg h-9", isCollapsed && "justify-center")}
                      onClick={() => setIsCollapsed(!isCollapsed)}
                    >
                      <CollapseIcon className={cn("h-4 w-4 transition-transform duration-350 ease-[cubic-bezier(0.2,0,0,1)]", isCollapsed && "rotate-180")} />
                      {!isCollapsed && <span className="transition-opacity duration-200 ease-[cubic-bezier(0.2,0,0,1)]">Colapsar</span>}
                    </Button>
                  </TooltipTrigger>
                  {isCollapsed && (
                    <TooltipContent side="right">
                      <p>{isCollapsed ? "Expandir" : "Extraer"}</p>
                    </TooltipContent>
                  )}
                </Tooltip>
              </li>
            </ul>
          </TooltipProvider>
        </nav>

        <div className={cn("p-4 border-t border-sidebar-border/40", isCollapsed && "flex justify-center")}>
          {user ? (
            <Popover open={isMenuOpen} onOpenChange={setMenuOpenSafely}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  className={cn(
                    "h-auto w-full justify-start gap-3 rounded-xl px-2 py-2 text-left text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]",
                    isCollapsed && "w-auto justify-center rounded-full p-0"
                  )}
                  aria-label="Abrir menú de usuario"
                >
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-primary-foreground transition-transform duration-200 ease-[cubic-bezier(0.2,0,0,1)] hover:scale-105">
                    <span className="font-medium text-xs">{profileInitials}</span>
                  </div>
                  {!isCollapsed && (
                    <div className="min-w-0 flex-1 transition-opacity duration-200 ease-[cubic-bezier(0.2,0,0,1)]">
                      <p className="truncate text-sm font-medium">{profileName}</p>
                      <p className="truncate text-xs text-muted-foreground">{profileEmail}</p>
                    </div>
                  )}
                </Button>
              </PopoverTrigger>
              <PopoverContent
                side={isCollapsed ? "right" : "top"}
                align={isCollapsed ? "end" : "start"}
                sideOffset={12}
                className="w-72 rounded-xl border border-border/40 p-2 shadow-[var(--cursor-shadow-lg)] animate-premium-scale-in"
              >
                <div className="mb-2 rounded-lg border border-border/30 bg-muted/30 px-3 py-2.5">
                  <p className="truncate text-sm font-medium">{profileName}</p>
                  <p className="truncate text-xs text-muted-foreground">{profileEmail}</p>
                </div>
                <div className="flex flex-col gap-1">
                  <Button
                    asChild
                    type="button"
                    variant="ghost"
                    className="justify-start rounded-xl"
                    onClick={() => setIsMenuOpen(false)}
                  >
                    <Link href="/settings" prefetch>
                      <Settings className="h-4 w-4" />
                      Configuración
                    </Link>
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    className="justify-start rounded-xl text-destructive hover:text-destructive"
                    onClick={() => void handleSignOut()}
                    disabled={isSigningOut}
                  >
                    <LogOut className="h-4 w-4" />
                    {isSigningOut ? "Cerrando..." : "Cerrar Sesión"}
                  </Button>
                </div>
              </PopoverContent>
            </Popover>
          ) : (
            <TooltipProvider delayDuration={0}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    asChild
                    variant="ghost"
                    className={cn(
                      "w-full justify-start gap-2 text-sidebar-foreground font-normal hover:bg-sidebar-accent/60 transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)] rounded-lg h-9",
                      isCollapsed && "justify-center"
                    )}
                    data-testid="sidebar-sign-in"
                  >
                    <Link href="/login" prefetch>
                      <LogIn className="h-4 w-4" />
                      {!isCollapsed && <span>Iniciar Sesión</span>}
                    </Link>
                  </Button>
                </TooltipTrigger>
                {isCollapsed && (
                  <TooltipContent side="right">
                    <p>Iniciar Sesión</p>
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
      </div>
    </div>
  )
}
