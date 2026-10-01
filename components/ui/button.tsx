import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)] disabled:pointer-events-none disabled:opacity-40 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:outline-none focus-visible:shadow-[var(--cursor-focus)] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive select-none",
  {
    variants: {
      variant: {
        default:
          "bg-foreground text-background shadow-xs hover:bg-foreground/90 hover:shadow-sm active:scale-[0.97] active:shadow-none active:bg-foreground/95",
        destructive:
          "bg-destructive text-white shadow-xs hover:bg-destructive/90 hover:shadow-md focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 dark:bg-destructive/60 active:scale-[0.97] active:shadow-none",
        outline:
          "border border-border/60 bg-background/50 shadow-2xs hover:bg-secondary/80 hover:border-border hover:text-foreground hover:shadow-xs active:scale-[0.98] active:bg-secondary/60",
        secondary:
          "bg-secondary text-secondary-foreground shadow-xs hover:bg-secondary/80 hover:shadow-sm active:scale-[0.97] active:shadow-none active:bg-secondary/90",
        ghost:
          "hover:bg-secondary/70 hover:text-foreground active:bg-secondary/90 transition-colors duration-150",
        accent:
          "bg-accent text-accent-foreground shadow-xs hover:bg-accent/90 hover:shadow-md active:scale-[0.97] active:shadow-none",
        link: "text-accent underline-offset-4 hover:underline hover:text-[var(--cursor-danger)] transition-all duration-150",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        sm: "h-8 rounded-md gap-1.5 px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }) {
  const Comp = asChild ? Slot : "button"

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
