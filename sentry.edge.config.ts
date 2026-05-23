import * as Sentry from "@sentry/nextjs";

// Configuración para el Edge Runtime de Next.js (middleware.ts, Edge API Routes).
// El Edge Runtime tiene un entorno más restringido que Node.js — sin Replay ni Profiling.
Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,

  enabled: !!process.env.NEXT_PUBLIC_SENTRY_DSN,

  // Mínimo sampling en edge — priorizar captura de errores sobre performance.
  tracesSampleRate: 0.05,
});
