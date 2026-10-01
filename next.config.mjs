// En: PromData/next.config.mjs
import { withSentryConfig } from "@sentry/nextjs";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // [P0-5 FIX 2026-07-25] Gates de build eliminados:
  // - 'typescript.ignoreBuildErrors: true' permitía merges con errores de tipo.
  //   Ahora el build FALLA si tsc falla (9 errores corregidos en Sprint 1).
  // - 'eslint.ignoreDuringBuilds' era clave muerta: Next.js 16 ya no soporta
  //   la clave 'eslint' en config (warning en cada dev server start). El gate
  //   de lint debe vivir como paso CI propio (Fase 6), no como flag de build.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  images: {
    unoptimized: true,
  },
  serverExternalPackages: ['@duckdb/duckdb-wasm'],
  webpack: (config, { dev }) => {
    if (dev) {
      config.watchOptions = {
        ...config.watchOptions,
        ignored: [
          '**/backend/**',
          '**/test_env/**',
          '**/test-results/**',
        ],
      };
      console.log('[Webpack] watchOptions.ignored aplicado:', JSON.stringify(config.watchOptions.ignored));
    }
    return config;
  },
  async headers() {
    const apiOrigin = (process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '').replace(/^https?:\/\//, '');
    const wssAllowed = process.env.NODE_ENV === 'development'
      ? `ws://localhost:3000 ws://${apiOrigin} http://${apiOrigin}`
      : `https://${apiOrigin}`;

    return [
      {
        source: '/:path*',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: *.vercel-insights.com *.vercel-scripts.com *.sentry.io",
              "style-src 'self' 'unsafe-inline'",
              "img-src * blob: data:",
              "media-src 'none'",
              // [QW-4 FIX 2026-07-25] https://cdn.jsdelivr.net es requerido por
              // DuckDB-WASM: lib/duckdb-engine.ts fetchea el worker script
              // (línea ~225) y el módulo .wasm desde ese CDN. Sin este origen
              // en connect-src, el cross-filter falla SOLO en producción
              // (donde estos headers sí se aplican). El worker se construye
              // desde blob: (worker-src blob: ya permitido), pero el fetch()
              // inicial quedaba bloqueado por CSP.
              // Se elimina además 'https://o*.ingest.sentry.io': fuente CSP
              // inválida (wildcard parcial de label) que los browsers ignoran;
              // 'https://*.sentry.io' ya cubre los endpoints de ingest.
              `connect-src 'self' https://cdn.jsdelivr.net https://*.supabase.co https://*.sentry.io wss://*.sentry.io ${wssAllowed}`,
              "font-src 'self'",
              "object-src 'none'",
              "frame-src 'self'",
              "worker-src 'self' blob:",
              "base-uri 'self'",
              "form-action 'self'",
            ].join('; '),
          },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=63072000; includeSubDomains; preload',
          },
          {
            key: 'X-Frame-Options',
            value: 'DENY',
          },
          {
            key: 'X-Content-Type-Options',
            value: 'nosniff',
          },
          {
            key: 'Referrer-Policy',
            value: 'strict-origin-when-cross-origin',
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
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,

  // Silenciar logs del plugin en la consola de build (reduce ruido en CI).
  silent: true,

  // Subir source maps de archivos adicionales del cliente para mejor stack traces.
  widenClientFileUpload: true,

  // Ocultar source maps del bundle público — solo existen en Sentry.
  hideSourceMaps: true,

  // Eliminar el logger de Sentry del bundle de producción (ahorra ~3KB).
  disableLogger: true,

  // No crear monitores automáticos de Vercel Cron (no usamos cron jobs en Vercel).
  automaticVercelMonitors: false,
});
