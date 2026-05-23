import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,

  // Solo activo si el DSN está configurado en el entorno.
  // En desarrollo local sin DSN configurado, es un no-op.
  enabled: !!process.env.NEXT_PUBLIC_SENTRY_DSN,

  environment: process.env.NODE_ENV,

  // Captura el 100% de errores.
  // Solo el 5% de transacciones de performance (para no saturar el tier gratuito).
  tracesSampleRate: 0.05,

  // Session Replay: solo en sesiones donde ocurre un error.
  // 0% en sesiones normales para conservar cuota del tier gratuito.
  replaysOnErrorSampleRate: 1.0,
  replaysSessionSampleRate: 0.0,

  integrations: [
    Sentry.replayIntegration({
      // Enmascarar texto e imágenes del usuario por privacidad (GDPR-safe).
      maskAllText: true,
      blockAllMedia: true,
    }),
  ],
});
