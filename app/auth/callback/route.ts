// En: app/auth/callback/route.ts

import { cookies } from 'next/headers'
import { NextResponse } from 'next/server'
import { type CookieOptions, createServerClient } from '@supabase/ssr'

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  // if "next" is in param, use it as the redirect URL
  const next = searchParams.get('next') ?? '/'

  if (code) {
    const cookieStore = await cookies()
    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          async get(name: string) {
            return cookieStore.get(name)?.value
          },
          async set(name: string, value: string, options: CookieOptions) {
            try {
              console.log(`CALLBACK COOKIE: Intentando establecer la cookie '${name}'`); // <-- LOG DE DIAGNÓSTICO
              cookieStore.set({ name, value, ...options })
            } catch (error) {
              console.error(`CALLBACK COOKIE ERROR: Falló al establecer la cookie '${name}'`, error); // <-- LOG DE ERROR
            }
          },
          async remove(name: string, options: CookieOptions) {
            try {
              cookieStore.delete({ name, ...options })
            } catch (error) {
              // Ignorar errores
            }
          },
        },
      }
    )
    
    // --- INICIO DEL BLOQUE DE DIAGNÓSTICO ---
    console.log("CALLBACK: Recibido código, intentando intercambiar por sesión...");
    const { data, error } = await supabase.auth.exchangeCodeForSession(code);

    if (error) {
      console.error("CALLBACK ERROR: Supabase devolvió un error:", error.message);
    } else {
      console.log("CALLBACK ÉXITO: Sesión obtenida de Supabase. ¿Contiene usuario?", !!data.user);
    }
    // --- FIN DEL BLOQUE DE DIAGNÓSTICO ---

    if (!error) {
      return NextResponse.redirect(`${origin}${next}`)
    }
  }
}