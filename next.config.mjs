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
