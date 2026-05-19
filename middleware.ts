// En: middleware.ts

import { createServerClient, type CookieOptions } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'

export async function middleware(request: NextRequest) {
  // Debug opcional y liviano para desarrollo local.
  if (process.env.NODE_ENV !== 'production' && process.env.NEXT_PUBLIC_DEBUG_MIDDLEWARE === '1') {
    console.log(
      `MIDDLEWARE: ${request.nextUrl.pathname} | cookies=${request.cookies.getAll().length}`
    );
  }
  
  // 1. Creamos una respuesta inicial que se pasará a través de toda la cadena.
  let response = NextResponse.next({
    request: {
      headers: request.headers,
    },
  })

  // 2. Creamos el cliente de Supabase para el servidor.
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        get(name: string) {
          return request.cookies.get(name)?.value
        },
        set(name: string, value: string, options: CookieOptions) {
          // Si Supabase necesita establecer una cookie, la añadimos a la *única* respuesta.
          response.cookies.set({
            name,
            value,
            ...options,
          })
        },
        remove(name: string, options: CookieOptions) {
          // Si Supabase necesita eliminar una cookie, lo hacemos en la *única* respuesta.
          response.cookies.set({
            name,
            value: '',
            ...options,
          })
        },
      },
    }
  )

  // 3. Esta línea es crucial: refresca la sesión del usuario si ha expirado
  // y asegura que la cookie de sesión esté siempre actualizada.
  await supabase.auth.getUser()

  // 4. Devolvemos la respuesta final, que ahora contiene la sesión correcta.
  return response
}

export const config = {
  matcher: [
    /*
     * Aplica middleware solo a rutas de aplicación.
     * Excluye estáticos/medios para evitar latencia innecesaria por refresh de sesión.
     */
    '/((?!_next/static|_next/image|favicon.ico|\\.well-known|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|txt|xml|map)$).*)',
  ],
}
