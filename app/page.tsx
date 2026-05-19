import { Suspense } from "react"
import { Sidebar } from "@/components/sidebar"
import { ChatInterface } from "@/components/chat-interface"
import { WorkspaceCanvas } from "@/components/workspace-canvas"

function DashboardPageContent() {
  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="border-b border-border px-6 py-4 shrink-0">
          <div className="flex items-center justify-between">
            <h1 className="text-lg font-semibold text-foreground"></h1>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                LB
              </div>
            </div>
          </div>
        </header>

        {/* 
          COPILOT LAYOUT — CSS Grid (immune to flexbox min-content blowout)
          grid-template-columns: 1fr 420px → workspace takes remaining space, chat is RIGIDLY 420px
          No content, no matter how wide, can ever change the 420px column.
        */}
        <div 
          className="flex-1 min-h-0 overflow-hidden"
          style={{ display: 'grid', gridTemplateColumns: '1fr 420px' }}
        >
          {/* Workspace Canvas — column 1 (1fr = fills remaining) */}
          <div className="min-w-0 min-h-0 overflow-hidden">
            <WorkspaceCanvas />
          </div>

          {/* Chat Panel — column 2 (RIGID 420px, enforced by grid) */}
          <div className="min-w-0 min-h-0 overflow-hidden border-l border-border bg-background flex flex-col shadow-[-2px_0_8px_rgba(0,0,0,0.04)] dark:shadow-[-2px_0_8px_rgba(0,0,0,0.2)]">
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
        <header className="border-b border-border px-6 py-4 shrink-0">
          <div className="flex items-center justify-between">
            <h1 className="text-lg font-semibold text-foreground"></h1>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                LB
              </div>
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
