"use client"

import { Auth } from '@supabase/auth-ui-react'
import { ThemeSupa } from '@supabase/auth-ui-shared'
import { createClient } from '@/lib/supabase-client'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

export default function LoginPage() {
  const supabase = createClient()
  const router = useRouter()
  const [sessionChecked, setSessionChecked] = useState(false)

  useEffect(() => {
    const checkSession = async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (session) {
        router.push('/');
      } else {
        setSessionChecked(true);
      }
    };

    checkSession();

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_IN') {
        router.push('/');
      }
    });

    return () => subscription.unsubscribe();
  }, [supabase, router]);
  
  if (!sessionChecked) {
    return null; 
  }

  return (
    <div className="flex justify-center items-center min-h-screen bg-background p-4">
      <div className="w-full max-w-sm p-8 space-y-6 bg-card text-card-foreground rounded-xl border shadow-sm">
        <div className="text-center">
            <h1 className="text-3xl font-semibold">Bienvenido</h1>
            <p className="text-muted-foreground mt-2 text-sm">Inicia sesión para empezar a analizar tus datos</p>
        </div>
        <Auth
          supabaseClient={supabase}
          appearance={{ theme: ThemeSupa }}
          providers={['google', 'github']}
          theme="dark"
          redirectTo={`${location.origin}/auth/callback`}
        />
      </div>
    </div>
  )
}