"use client"

import { useTheme } from "next-themes"
import { Toaster as Sonner, ToasterProps } from "sonner"

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = "system" } = useTheme()

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      className="toaster group"
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--success-bg": "oklch(0.96 0.02 160)",
          "--success-text": "oklch(0.35 0.15 160)",
          "--success-border": "oklch(0.7 0.12 160 / 0.3)",
          "--error-bg": "oklch(0.96 0.02 15)",
          "--error-text": "oklch(0.55 0.195 15)",
          "--error-border": "oklch(0.7 0.15 15 / 0.3)",
          "--info-bg": "oklch(0.96 0.02 258)",
          "--info-text": "oklch(0.45 0.15 258)",
          "--info-border": "oklch(0.7 0.12 258 / 0.3)",
        } as React.CSSProperties
      }
      {...props}
    />
  )
}

export { Toaster }
