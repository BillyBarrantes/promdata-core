// En: lib/supabase-provider.tsx
"use client";

import { createContext, useContext, useState, useMemo } from 'react';
import { createClient } from '@/lib/supabase-client';

// [P0-5 FIX] '@supabase/supabase-js' no es dependencia directa (pnpm no
// hoista transitivas). El tipo se deriva del factory real, que sí está
// tipado vía '@supabase/ssr'.
type SupabaseClient = ReturnType<typeof createClient>;

type SupabaseContext = {
  supabase: SupabaseClient;
};

const Context = createContext<SupabaseContext | undefined>(undefined);

export default function SupabaseProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [supabase] = useState(() => createClient());

    const value = useMemo(() => ({ supabase }), [supabase]);

  return (
    <Context.Provider value={value}>
      <>{children}</>
    </Context.Provider>
  );
}

export const useSupabase = () => {
  const context = useContext(Context);

  if (context === undefined) {
    throw new Error('useSupabase must be used inside SupabaseProvider');
  }

  return context.supabase;
};