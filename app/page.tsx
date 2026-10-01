"use client"

import { Suspense, useState } from "react"
import { Sidebar } from "@/components/sidebar"
import { ChatInterface } from "@/components/chat-interface"
import { WorkspaceCanvas } from "@/components/workspace-canvas"
import { HeaderUserAvatar } from "@/components/header-user-avatar"
import { Button } from "@/components/ui/button"
import { PanelRightClose, PanelRightOpen } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

function DashboardPageContent() {
  const [isChatOpen, setIsChatOpen] = useState(true)

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="mb-6 border-b border-border/20 px-6 py-5 shrink-0 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl sm:text-4xl tracking-tight text-foreground leading-tight">Inicio</h1>
              <p className="mt-1.5 text-base font-light text-muted-foreground">Espacio de trabajo y análisis en vivo.</p>
            </div>
            <div className="flex items-center gap-1">
              <TooltipProvider delayDuration={0}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 text-muted-foreground hover:text-foreground transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)] hover:scale-105 active:scale-95"
                      onClick={() => setIsChatOpen(!isChatOpen)}
                      aria-label={isChatOpen ? "Ocultar chat" : "Mostrar chat"}
                    >
                      {isChatOpen ? (
                        <PanelRightClose className="h-4 w-4" />
                      ) : (
                        <PanelRightOpen className="h-4 w-4" />
                      )}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    <p>{isChatOpen ? "Ocultar chat" : "Mostrar chat"}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <HeaderUserAvatar />
            </div>
          </div>
        </header>

        {/* 
          COPILOT LAYOUT — CSS Grid (immune to flexbox min-content blowout)
          grid-template-columns: 1fr 420px → workspace takes remaining space, chat is RIGIDLY 420px
          When chat is collapsed: 1fr 0px → workspace takes full width
          Smooth transition via CSS transition on grid-template-columns.
        */}
        <div 
          className="flex-1 min-h-0 overflow-hidden"
          style={{ 
            display: 'grid', 
            gridTemplateColumns: isChatOpen ? '1fr 420px' : '1fr 0px',
            transition: 'grid-template-columns 350ms cubic-bezier(0.2, 0, 0, 1)',
          }}
        >
          {/* Workspace Canvas — column 1 (1fr = fills remaining) */}
          <div className="min-w-0 min-h-0 overflow-hidden">
            <WorkspaceCanvas />
          </div>

          {/* Chat Panel — column 2 (420px or 0px, animated) */}
          <div 
            className="min-w-0 min-h-0 overflow-hidden border-l border-border/40 bg-background flex flex-col shadow-[var(--cursor-shadow-xs)]"
            style={{
              opacity: isChatOpen ? 1 : 0,
              pointerEvents: isChatOpen ? 'auto' : 'none',
              transition: 'opacity 250ms cubic-bezier(0.2, 0, 0, 1)',
            }}
          >
            <ChatInterface />
          </div>
        </div>
      </main>
    </div>
  )
}

function DashboardPageFallback() {
  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="border-b border-border/20 px-6 py-5 shrink-0">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl sm:text-4xl tracking-tight text-foreground leading-tight">Inicio</h1>
              <p className="mt-1.5 text-base font-light text-muted-foreground">Espacio de trabajo y análisis en vivo.</p>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full bg-muted/60 animate-pulse shrink-0" />
            </div>
          </div>
        </header>
        <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
          Cargando espacio de trabajo...
        </div>
      </main>
    </div>
  )
}

export default function DashboardPage() {
  return (
    <Suspense fallback={<DashboardPageFallback />}>
      <DashboardPageContent />
    </Suspense>
  )
}
