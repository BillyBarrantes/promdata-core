"use client"

import { useState, useRef, useCallback } from "react"
import { toast } from "sonner"

export function useVoiceRecognition(onTranscript: (text: string) => void) {
  const [isListening, setIsListening] = useState(false)
  const recognitionRef = useRef<any>(null)

  const toggleListening = useCallback(() => {
    if (isListening) {
      recognitionRef.current?.stop()
      setIsListening(false)
      return
    }

    if (typeof window === "undefined") return

    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (!SpeechRecognition) {
      toast.error("Tu navegador no soporta entrada de voz.")
      return
    }

    const recognition = new SpeechRecognition()
    recognition.lang = "es-ES"
    recognition.continuous = false
    recognition.interimResults = false

    recognition.onstart = () => {
      setIsListening(true)
      toast.info("Escuchando...")
    }

    recognition.onresult = (event: any) => {
      const transcript = event.results[0][0].transcript
      onTranscript(transcript)
    }

    recognition.onerror = (event: any) => {
      console.error("Error de voz:", event.error)
      setIsListening(false)
      if (event.error !== "no-speech") {
        toast.error("Error al escuchar.")
      }
    }

    recognition.onend = () => {
      setIsListening(false)
    }

    recognitionRef.current = recognition
    recognition.start()
  }, [isListening, onTranscript])

  return {
    isListening,
    toggleListening,
  }
}
