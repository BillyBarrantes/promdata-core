"use client"

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Home, PieChart, Database, BookOpen, Library, LogOut, UserCircle2 } from "lucide-react";
import { CollapseIcon } from "./icons/collapse-icon";
import { atom, useAtom, useSetAtom } from "jotai";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useSupabase } from "@/lib/supabase-provider";
import { toast } from "sonner";
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

export const sidebarStateAtom = atom(false);

const sidebarItems = [
  { id: "inicio", label: "Inicio", path: "/", icon: <Home className="w-5 h-5" /> },
  { id: "dashboard", label: "Dashboard", path: "/dashboard", icon: <PieChart className="w-5 h-5" /> },
  { id: "cargar-datos", label: "Cargar Datos", path: "/cargar-datos", icon: <Database className="w-5 h-5" /> },
  { id: "conocimiento", label: "Conocimiento", path: "/conocimiento", icon: <Library className="w-5 h-5" /> },
  { id: "glosario", label: "Glosario", path: "/glosario", icon: <BookOpen className="w-5 h-5" /> },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const [isCollapsed, setIsCollapsed] = useAtom(sidebarStateAtom);
  const supabase = useSupabase();
  const setFilters = useSetAtom(filtersAtom);
  const setCrossFilters = useSetAtom(crossFilterAtom);
  const setDuckdbReady = useSetAtom(duckdbReadyAtom);
  const setWorkspaceItems = useSetAtom(workspaceItemsAtom);
  const setWorkspaceRenderState = useSetAtom(workspaceRenderStateAtom);
  const setPresentationState = useSetAtom(presentationStateAtom);
  const setDrillDown = useSetAtom(drillDownAtom);
  const [user, setUser] = useState<any | null>(null);
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);

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

  const profileName = useMemo(() => {
    const candidates = [
      user?.user_metadata?.full_name,
      user?.user_metadata?.name,
      user?.user_metadata?.preferred_username,
      user?.email?.split("@")[0],
    ];
    for (const candidate of candidates) {
      if (typeof candidate === "string" && candidate.trim()) {
        return candidate.trim();
      }
    }
    return "Mi cuenta";
  }, [user]);

  const profileEmail = useMemo(() => {
    return typeof user?.email === "string" && user.email.trim() ? user.email.trim() : "Sin correo visible";
  }, [user]);

  const profileInitials = useMemo(() => {
    const source = profileName.trim() || "U";
    const parts = source.split(/\s+/).filter(Boolean).slice(0, 2);
    return parts.map((part) => part.charAt(0).toUpperCase()).join("") || "U";
  }, [profileName]);

  const handleSignOut = async () => {
    if (isSigningOut) return;

    setIsSigningOut(true);
    try {
      setMenuOpenSafely(false);
      setIsProfileOpen(false);

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

      await clearPromDataBrowserState();
      await supabase.auth.signOut();
      window.location.replace("/login");
    } catch (error) {
      console.error("Error cerrando sesión", error);
      toast.error("No se pudo cerrar sesión correctamente.");
      setIsSigningOut(false);
    }
  };

  const setMenuOpenSafely = (nextOpen: boolean) => {
    if (isSigningOut) return;
    setIsMenuOpen(nextOpen);
  };

  return (
    <div className={cn(
      "bg-sidebar border-r border-sidebar-border flex h-screen min-h-screen shrink-0 flex-col transition-all duration-300 ease-in-out",
      isCollapsed ? "w-20" : "w-56"
    )}>
      <div className="p-4 border-b border-sidebar-border">
        <div className={cn("flex items-center gap-2", isCollapsed && "justify-center")}>
          <div className="w-8 h-8 bg-blue-600 rounded-md flex items-center justify-center flex-shrink-0">
            <span className="text-white font-bold text-sm">P</span>
          </div>
          {!isCollapsed && <span className="font-semibold text-sidebar-foreground">PromData</span>}
        </div>
      </div>

      {/* Navegación Principal con Tooltips */}
      <nav className="flex-1 p-2">
        <TooltipProvider delayDuration={0}>
          <ul className="space-y-2">
            {sidebarItems.map((item) => (
              <li key={item.id}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      className={cn(
                        "w-full justify-start gap-2 text-sidebar-foreground font-light hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                        pathname === item.path && "bg-sidebar-accent text-sidebar-accent-foreground font-semibold",
                        isCollapsed && "justify-center"
                      )}
                      onClick={() => router.push(item.path)}
                      data-testid={`sidebar-nav-${item.id}`}
                    >
                      {item.icon}
                      {!isCollapsed && <span>{item.label}</span>}
                    </Button>
                  </TooltipTrigger>
                  {/* El Tooltip solo se muestra si la barra está colapsada */}
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
        <nav className="p-2 border-t border-sidebar-border">
          <TooltipProvider delayDuration={0}>
            <ul className="space-y-2">
              <li>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      className={cn("w-full justify-start gap-2 text-sidebar-foreground font-light", isCollapsed && "justify-center")}
                      onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                    >
                      {/* 3. Ícono de modo oscuro corregido a .svg */}
                      <img src="/moon.svg" alt="Modo oscuro" className="h-5 w-5" />
                      {!isCollapsed && <span>Modo oscuro</span>}
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
                      className={cn("w-full justify-start gap-2 text-sidebar-foreground font-light", isCollapsed && "justify-center")}
                      onClick={() => setIsCollapsed(!isCollapsed)}
                    >
                      <CollapseIcon className={cn("h-5 w-5 transition-transform", isCollapsed && "rotate-180")} />
                      {!isCollapsed && <span>Extraer</span>}
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

        <div className={cn("p-4 border-t border-sidebar-border", isCollapsed && "flex justify-center")}>
          <Popover open={isMenuOpen} onOpenChange={setMenuOpenSafely}>
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                className={cn(
                  "h-auto w-full justify-start gap-3 rounded-xl px-2 py-2 text-left text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                  isCollapsed && "w-auto justify-center rounded-full p-0"
                )}
                aria-label="Abrir menú de usuario"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gray-700 text-white">
                  <span className="font-bold text-sm">{profileInitials}</span>
                </div>
                {!isCollapsed && (
                  <div className="min-w-0 flex-1">
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
              className="w-72 rounded-2xl border border-border/70 p-2 shadow-xl"
            >
              <div className="mb-2 rounded-xl border border-border/60 bg-muted/30 px-3 py-3">
                <p className="truncate text-sm font-semibold">{profileName}</p>
                <p className="truncate text-xs text-muted-foreground">{profileEmail}</p>
              </div>
              <div className="flex flex-col gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  className="justify-start rounded-xl"
                  onClick={() => {
                    setIsMenuOpen(false);
                    setIsProfileOpen(true);
                  }}
                >
                  <UserCircle2 className="h-4 w-4" />
                  Mi Perfil
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
        </div>
      </div>

      <Dialog open={isProfileOpen} onOpenChange={setIsProfileOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Mi Perfil</DialogTitle>
            <DialogDescription>Vista de solo lectura del usuario autenticado.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="flex items-center gap-3 rounded-2xl border border-border/70 bg-muted/20 p-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gray-700 text-white">
                <span className="font-bold text-base">{profileInitials}</span>
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{profileName}</p>
                <p className="truncate text-xs text-muted-foreground">{profileEmail}</p>
              </div>
            </div>
            <div className="space-y-3">
              <div className="rounded-xl border border-border/70 px-3 py-2">
                <p className="text-xs text-muted-foreground">Usuario</p>
                <p className="break-all text-sm">{user?.id || "No disponible"}</p>
              </div>
              <div className="rounded-xl border border-border/70 px-3 py-2">
                <p className="text-xs text-muted-foreground">Proveedor</p>
                <p className="text-sm">{user?.app_metadata?.provider || "email"}</p>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setIsProfileOpen(false)}>
              Cerrar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
