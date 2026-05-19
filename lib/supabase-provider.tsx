// En: lib/supabase-provider.tsx
"use client";

import { createContext, useContext, useState, useMemo } from 'react';
import { createClient } from '@/lib/supabase-client';
import type { SupabaseClient } from '@supabase/supabase-js';

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