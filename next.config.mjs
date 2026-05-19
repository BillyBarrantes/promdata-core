// En: PromData/next.config.mjs

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
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: *.vercel-insights.com",
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

export default nextConfig
