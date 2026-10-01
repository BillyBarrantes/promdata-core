"use client"

import { Auth } from '@supabase/auth-ui-react'
import { ThemeSupa } from '@supabase/auth-ui-shared'
import { createClient } from '@/lib/supabase-client'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

/**
 * Normaliza el parámetro `?next=` a un path interno seguro.
 * Bloquea URLs externas (`//evil.com`) y valores no-path.
 */
function resolveSafeNext(rawNext: string | null): string {
  if (!rawNext) return '/'
  if (!rawNext.startsWith('/') || rawNext.startsWith('//')) return '/'
  return rawNext
}

export default function LoginPage() {
  const supabase = createClient()
  const router = useRouter()
  const [sessionChecked, setSessionChecked] = useState(false)
  const [callbackUrl, setCallbackUrl] = useState('')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const safeNext = resolveSafeNext(params.get('next'))
    setCallbackUrl(
      `${window.location.origin}/auth/callback?next=${encodeURIComponent(safeNext)}`
    )

    const checkSession = async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (session) {
        router.replace(safeNext);
      } else {
        setSessionChecked(true);
      }
    };

    checkSession();

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event) => {
      if (event === 'SIGNED_IN') {
        router.replace(safeNext);
      }
    });

    return () => subscription.unsubscribe();
  }, [supabase, router]);
  
  if (!sessionChecked || !callbackUrl) {
    return null; 
  }

  return (
    <div className="flex justify-center items-center min-h-screen bg-background p-4">
      <div className="w-full max-w-md p-10 space-y-6 bg-card text-card-foreground rounded-xl border border-border/60 shadow-[var(--cursor-shadow-lg)] animate-premium-entrance">
        <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Bienvenido</h1>
            <p className="text-muted-foreground mt-2 text-sm">Inicia sesión para empezar a analizar tus datos</p>
        </div>
        <Auth
          supabaseClient={supabase}
          appearance={{ theme: ThemeSupa }}
          providers={['google', 'github']}
          redirectTo={callbackUrl}
        />
      </div>
    </div>
  )
}
