// En: PromData/next.config.mjs
import { withSentryConfig } from "@sentry/nextjs";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // [FIX 2026-06-11] Removido `eslint: { ignoreDuringBuilds: true }` —
  // Next.js 15+ ya no soporta esta opcion en next.config.mjs. Si se
  // necesita silenciar errores de lint en build, usar ESLINT_NO_DEV_ERRORS
  // o NEXT_DISABLE_ESLINT en env vars, o ejecutar eslint por separado.
  typescript: {
    ignoreBuildErrors: true,
  },
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  images: {
    unoptimized: true,
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              // CORRECCIÓN: Añadido *.vercel-insights.com para permitir los scripts de analíticas de Vercel
              // blob: necesario como fallback para navegadores que usan script-src cuando worker-src no está soportado
              // *.sentry.io necesario para que Sentry pueda enviar eventos y el SDK cargue correctamente
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: *.vercel-insights.com *.sentry.io",
              "style-src 'self' 'unsafe-inline'",
              "img-src * blob: data:",
              "media-src 'none'",
              "connect-src *",
              "font-src 'self'",
              "object-src 'none'",
              "frame-src 'self'",
              // 🦆 [DuckDB-WASM] Worker desde Blob URL (fetch CDN → Blob → createObjectURL)
              "worker-src 'self' blob:",
              "base-uri 'self'",
              "form-action 'self'",
            ].join('; '),
          },
        ],
      },
    ]
  },
}

// ---------------------------------------------------------------------------
// Sentry: withSentryConfig envuelve nextConfig para activar:
//   - Upload automático de Source Maps a Sentry en cada build de producción.
//   - Tree-shaking del SDK en el cliente para minimizar bundle size.
//   - Las variables SENTRY_ORG, SENTRY_PROJECT y SENTRY_AUTH_TOKEN se
//     inyectan desde Vercel → Settings → Environment Variables.
// ---------------------------------------------------------------------------
// [FIX 2026-06-11] Las opciones `disableLogger` y `automaticVercelMonitors`
// fueron deprecadas en @sentry/nextjs >=9. Las nuevas opciones viven
// dentro de `webpack.*` y se pasan como objeto anidado:
//   - disableLogger: true           → webpack.treeshake.removeDebugLogging: true
//   - automaticVercelMonitors:false → webpack.automaticVercelMonitors: false
// (No soportado con Turbopack — mantener webpack config para builds prod.)
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,

  // Silenciar logs del plugin en la consola de build (reduce ruido en CI).
  silent: true,

  // Subir source maps de archivos adicionales del cliente para mejor stack traces.
  widenClientFileUpload: true,

  // Ocultar source maps del bundle público — solo existen en Sentry.
  hideSourceMaps: true,

  // Las opciones deprecadas se movieron a `webpack.*`.
  // Ver https://docs.sentry.io/platforms/javascript/guides/nextjs/configuration/tree-shaking/
  webpack: {
    // Eliminar el logger de Sentry del bundle de producción (ahorra ~3KB).
    treeshake: {
      removeDebugLogging: true,
    },
    // No crear monitores automáticos de Vercel Cron (no usamos cron jobs en Vercel).
    automaticVercelMonitors: false,
  },
});
