import type { Metadata } from 'next'
// import { GeistSans } from 'geist/font/sans'
// import { GeistMono } from 'geist/font/mono'
import { Analytics } from '@vercel/analytics/next'
import './globals.css'
import { Toaster } from "@/components/ui/sonner"
import { ThemeProvider } from "@/components/theme-provider"
import SupabaseProvider from '@/lib/supabase-provider';
import { PageTransition } from "@/components/page-transition";
import { UserPreferencesHydrator } from "@/components/user-preferences-hydrator";

export const metadata: Metadata = {
  title: 'PromData',
  description: 'Plataforma de analítica e inteligencia de datos',
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="es" suppressHydrationWarning>
      <body className="font-sans antialiased" suppressHydrationWarning>
        <SupabaseProvider>
          <ThemeProvider
            attribute="class"
            defaultTheme="system"
            enableSystem
            disableTransitionOnChange
          >
            <UserPreferencesHydrator />
            <PageTransition>{children}</PageTransition>
            <Analytics />
            <Toaster />
          </ThemeProvider>
        </SupabaseProvider>
      </body>
    </html>
  )
}