"use client";

import { useCallback, useEffect, useState } from "react";
import { Palette, ShieldCheck, TriangleAlert, UserCog } from "lucide-react";
import { Sidebar } from "@/components/sidebar";
import { useSupabase } from "@/lib/supabase-provider";
import type { PromDataAuthUser } from "@/components/auth-types";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ProfileSection } from "@/components/settings/profile-section";
import { AppearanceSection } from "@/components/settings/appearance-section";
import { AccountSection } from "@/components/settings/account-section";
import { DangerZoneSection } from "@/components/settings/danger-zone-section";

const SETTINGS_TABS = [
  { value: "profile", label: "Perfil", icon: UserCog },
  { value: "appearance", label: "Apariencia", icon: Palette },
  { value: "account", label: "Cuenta", icon: ShieldCheck },
  { value: "danger", label: "Zona de peligro", icon: TriangleAlert },
] as const;

export default function SettingsPage() {
  const supabase = useSupabase();
  const [user, setUser] = useState<PromDataAuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;

    const loadUser = async () => {
      try {
        const {
          data: { user: currentUser },
          error,
        } = await supabase.auth.getUser();
        if (error) throw error;
        if (isMounted) setUser(currentUser ?? null);
      } catch (error) {
        console.error("[settings] No se pudo cargar el usuario:", error);
      } finally {
        if (isMounted) setIsLoading(false);
      }
    };

    void loadUser();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event: string, session: any) => {
      if (isMounted) setUser(session?.user ?? null);
    });

    return () => {
      isMounted = false;
      subscription.unsubscribe();
    };
  }, [supabase]);

  const handleUserUpdated = useCallback((nextUser: PromDataAuthUser) => {
    setUser(nextUser);
  }, []);

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />

      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="border-b border-border/20 px-6 py-5 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 sticky top-0 z-10 shrink-0">
          <div className="mx-auto w-full max-w-4xl">
            <h1 className="text-3xl sm:text-4xl tracking-tight text-foreground leading-tight">
              Configuración
            </h1>
            <p className="mt-1.5 text-base font-light text-muted-foreground">
              Gestiona tu perfil, preferencias y seguridad de la cuenta.
            </p>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto">
          <Tabs
            defaultValue="profile"
            className="mx-auto w-full max-w-4xl px-6"
          >
            <div className="sticky top-0 z-[5] -mx-6 border-b border-border/40 bg-background/95 px-6 backdrop-blur supports-[backdrop-filter]:bg-background/60">
              <TabsList className="h-auto w-full flex-nowrap items-center justify-start gap-1 overflow-x-auto rounded-none bg-transparent p-0">
                {SETTINGS_TABS.map((tab) => {
                  const Icon = tab.icon;
                  return (
                    <TabsTrigger
                      key={tab.value}
                      value={tab.value}
                      className="h-auto shrink-0 gap-2 rounded-none border-b-2 border-transparent bg-transparent px-3 py-3 text-sm font-medium text-muted-foreground shadow-none transition-colors duration-200 ease-[cubic-bezier(0.2,0,0,1)] hover:text-foreground data-[state=active]:border-accent data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none"
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      <span>{tab.label}</span>
                    </TabsTrigger>
                  );
                })}
              </TabsList>
            </div>

            <div className="py-8">
              <TabsContent value="profile">
                <ProfileSection
                  user={user}
                  isLoading={isLoading}
                  onUserUpdated={handleUserUpdated}
                />
              </TabsContent>
              <TabsContent value="appearance">
                <AppearanceSection />
              </TabsContent>
              <TabsContent value="account">
                <AccountSection user={user} />
              </TabsContent>
              <TabsContent value="danger">
                <DangerZoneSection />
              </TabsContent>
            </div>
          </Tabs>
        </div>
      </main>
    </div>
  );
}
