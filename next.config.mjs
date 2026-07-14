// En: PromData/next.config.mjs
import { withSentryConfig } from "@sentry/nextjs";

/** @type {import('next').NextConfig} */
const nextConfig = {
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
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
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: *.vercel-insights.com *.sentry.io",
              "style-src 'self' 'unsafe-inline'",
              "img-src * blob: data:",
              "media-src 'none'",
              `connect-src 'self' https://*.supabase.co https://*.sentry.io https://o*.ingest.sentry.io wss://*.sentry.io ${wssAllowed}`,
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
