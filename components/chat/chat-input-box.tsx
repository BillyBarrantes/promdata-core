"use client"

import React from "react"
import { Database, X, Mic, MicOff, SendHorizonal, Square } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export interface ChatInputBoxProps {
  message: string
  setMessage: (val: string | ((prev: string) => string)) => void
  isAnalyzing: boolean
  analysisFileId: string | null
  fileName: string | null
  onClearFile: () => void
  onSubmit: (e?: React.FormEvent) => void
  isListening: boolean
  onToggleListening: () => void
}

export function ChatInputBox({
  message,
  setMessage,
  isAnalyzing,
  analysisFileId,
  fileName,
  onClearFile,
  onSubmit,
  isListening,
  onToggleListening,
}: ChatInputBoxProps) {
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      onSubmit()
    }
  }

  return (
    <div className="w-full bg-background pt-4 pb-6 mt-auto z-10">
      <div className="w-full px-4">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            onSubmit(e)
          }}
          className="flex flex-col gap-2 relative bg-card/95 backdrop-blur-sm border border-border/60 p-3.5 rounded-2xl shadow-[var(--cursor-shadow-sm)] hover:border-border/80 hover:shadow-[var(--cursor-shadow-md)] focus-within:border-accent/40 focus-within:shadow-[var(--cursor-glow)] transition-all duration-300 ease-[cubic-bezier(0.2,0,0,1)]"
        >
          {fileName && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-secondary/80 text-foreground rounded-full w-fit mb-1 border border-border/50 text-xs shadow-2xs animate-premium-fade">
              <Database className="w-3.5 h-3.5 text-accent" />
              <span className="text-xs font-medium truncate max-w-[200px]">{fileName}</span>
              <button
                type="button"
                onClick={onClearFile}
                className="text-muted-foreground hover:text-destructive ml-0.5 transition-colors duration-150"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          )}

          <div className="flex items-end gap-2">
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                isAnalyzing
                  ? "Analizando datos..."
                  : analysisFileId
                  ? "Pregúntame sobre tus datos..."
                  : "Carga un archivo para empezar..."
              }
              className="flex-1 min-h-[40px] max-h-32 bg-transparent border-none outline-none focus:outline-none focus-visible:outline-none focus-visible:shadow-none focus:shadow-none shadow-none ring-0 focus:ring-0 resize-none py-2 px-1 text-sm text-foreground placeholder:text-muted-foreground"
              style={{ outline: "none", boxShadow: "none" }}
              disabled={isAnalyzing}
              data-testid="chat-input"
            />
            <div className="flex items-end gap-1 pb-0.5">
              <Button
                type="button"
                size="icon"
                variant="ghost"
                onClick={onToggleListening}
                className={cn(
                  "rounded-lg h-8 w-8 transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]",
                  isListening
                    ? "bg-destructive/10 text-destructive animate-pulse shadow-[0_0_8px_var(--destructive)]"
                    : "text-muted-foreground hover:bg-muted/80 hover:text-foreground hover:scale-105 active:scale-95"
                )}
                title="Entrada de Voz"
              >
                {isListening ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
              </Button>

              <Button
                type="submit"
                size="icon"
                disabled={(!message.trim() && !isAnalyzing) || (!analysisFileId && !isAnalyzing)}
                data-testid="chat-submit"
                className={cn(
                  "rounded-xl h-8 w-8 transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)]",
                  message.trim() || isAnalyzing
                    ? "bg-accent text-accent-foreground hover:bg-accent/90 hover:shadow-[0_2px_12px_oklch(0.72_0.14_28/0.3)] active:scale-[0.92]"
                    : "bg-muted text-muted-foreground"
                )}
              >
                {isAnalyzing ? (
                  <Square className="h-4 w-4 fill-current" />
                ) : (
                  <SendHorizonal className="h-4 w-4" />
                )}
              </Button>
            </div>
          </div>

        </form>
        <div className="text-center mt-2">
          <p className="text-[10px] text-muted-foreground">
            PromData AI puede cometer errores. Verifica la información importante.
          </p>
        </div>
      </div>
    </div>
  )
}
