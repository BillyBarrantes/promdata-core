"use client"

import React, { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog"
import { toast } from "sonner"
import { API_BASE_URL } from "@/lib/api-config"
import { fetchPresentationsShared } from "@/components/presentations-fetch"

import { useAtomValue, useSetAtom } from "jotai"
import { activePresentationIdAtom, presentationsListAtom } from "@/lib/state"

export interface SaveReportDialogProps {
  isOpen: boolean
  onClose: () => void
  reportToSave: any
  defaultTitle?: string
  fileId: string | null
  presentations?: any[]
  setPresentations?: (presentations: any[]) => void
  setActivePresentationId?: (id: string) => void
  getAccessToken: () => Promise<string | null>
}

export function SaveReportDialog({
  isOpen,
  onClose,
  reportToSave,
  defaultTitle = "",
  fileId,
  presentations: propsPresentations,
  setPresentations: propsSetPresentations,
  setActivePresentationId: propsSetActivePresentationId,
  getAccessToken,
}: SaveReportDialogProps) {
  const router = useRouter()
  const atomPresentations = useAtomValue(presentationsListAtom)
  const atomSetPresentations = useSetAtom(presentationsListAtom)
  const atomSetActivePresentationId = useSetAtom(activePresentationIdAtom)

  const presentations = propsPresentations ?? (Array.isArray(atomPresentations) ? atomPresentations : [])
  const setPresentations = propsSetPresentations ?? atomSetPresentations
  const setActivePresentationId = propsSetActivePresentationId ?? atomSetActivePresentationId
  const [reportTitle, setReportTitle] = useState(defaultTitle)
  const [selectedSavePresentationId, setSelectedSavePresentationId] = useState("")
  const [isSaveActionLoading, setIsSaveActionLoading] = useState(false)
  const [isSaveDestinationsLoading, setIsSaveDestinationsLoading] = useState(false)

  useEffect(() => {
    if (isOpen) {
      setReportTitle(defaultTitle || "")
      setSelectedSavePresentationId("")
    }
  }, [isOpen, defaultTitle])

  const refreshSaveDestinations = useCallback(async () => {
    setIsSaveDestinationsLoading(true)
    try {
      const accessToken = await getAccessToken()
      if (!accessToken) return

      const payload = await fetchPresentationsShared(accessToken)
      setPresentations(payload as any)
    } catch (error) {
      console.error("Error cargando presentaciones para guardado:", error)
    } finally {
      setIsSaveDestinationsLoading(false)
    }
  }, [getAccessToken, setPresentations])

  useEffect(() => {
    if (isOpen) {
      void refreshSaveDestinations()
    }
  }, [isOpen, refreshSaveDestinations])

  const handleConfirmSave = useCallback(async () => {
    if (!reportTitle.trim()) {
      toast.error("Por favor ingresa un título para el reporte.")
      return
    }

    if (!fileId) {
      toast.error("No hay un archivo activo para asociar el reporte.")
      return
    }

    setIsSaveActionLoading(true)
    try {
      const accessToken = await getAccessToken()
      if (!accessToken) return

      const destinationPresentationId = selectedSavePresentationId.trim()
      const reportResponse = await fetch(`${API_BASE_URL}/api/v1/reports`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          title: reportTitle,
          content: reportToSave,
          file_id: fileId,
          ...(destinationPresentationId ? { presentation_id: destinationPresentationId } : {}),
        }),
      })

      if (!reportResponse.ok) {
        throw new Error(`Error al guardar reporte (${reportResponse.status})`)
      }

      const reportPayload = await reportResponse.json().catch(() => ({}))
      const savedPresentation = reportPayload?.presentation
      const resolvedPresentationId =
        destinationPresentationId ||
        (typeof reportPayload?.presentation_id === "string" ? reportPayload.presentation_id : "") ||
        (typeof savedPresentation?.id === "string" ? savedPresentation.id : "")

      if (savedPresentation?.id) {
        const currentPresentations = Array.isArray(presentations) ? presentations : []
        const nextPresentations = [...currentPresentations]
        const existingIndex = nextPresentations.findIndex(
          (presentation: any) => presentation?.id === savedPresentation.id
        )

        if (existingIndex >= 0) {
          nextPresentations[existingIndex] = {
            ...nextPresentations[existingIndex],
            ...savedPresentation,
          }
        } else {
          nextPresentations.unshift(savedPresentation)
        }

        setPresentations(nextPresentations)
      }

      if (resolvedPresentationId) {
        setActivePresentationId(resolvedPresentationId)
      }

      const knownPresentations = Array.isArray(presentations) ? presentations : []
      const selectedPresentation = knownPresentations.find(
        (presentation: any) => presentation?.id === resolvedPresentationId
      )
      const resolvedPresentationName =
        (typeof savedPresentation?.name === "string" && savedPresentation.name.trim()) ||
        (typeof selectedPresentation?.name === "string" && selectedPresentation.name.trim()) ||
        null

      const successMessage = destinationPresentationId
        ? resolvedPresentationName
          ? `Reporte guardado en "${resolvedPresentationName}".`
          : "Reporte guardado en la presentación seleccionada."
        : "Reporte guardado en la presentación automática del archivo."

      toast.success(successMessage, {
        action: resolvedPresentationId
          ? {
              label: "Ir a la presentación",
              onClick: () =>
                router.push(`/dashboard?presentationId=${encodeURIComponent(resolvedPresentationId)}`),
            }
          : undefined,
      })

      setSelectedSavePresentationId("")
      onClose()
    } catch (error) {
      toast.error("No se pudo guardar el reporte.")
      console.error(error)
    } finally {
      setIsSaveActionLoading(false)
    }
  }, [
    fileId,
    getAccessToken,
    onClose,
    presentations,
    reportTitle,
    reportToSave,
    router,
    selectedSavePresentationId,
    setActivePresentationId,
    setPresentations,
  ])

  if (!isOpen || !reportToSave) return null

  return (
    <Dialog open={isOpen && !!reportToSave} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-md" data-testid="save-report-dialog">
        <DialogHeader>
          <DialogTitle>Guardar Reporte</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Título del Reporte</label>
            <input
              type="text"
              value={reportTitle}
              onChange={(e) => setReportTitle(e.target.value)}
              placeholder="Ej: Análisis de Ventas Q3"
              className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm outline-none focus:ring-2 focus:ring-ring/30"
              autoFocus
              data-testid="save-report-title-input"
              disabled={isSaveActionLoading}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Presentación destino (Opcional)</label>
            <select
              value={selectedSavePresentationId}
              onChange={(event) => setSelectedSavePresentationId(event.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm outline-none focus:ring-2 focus:ring-ring/30"
              disabled={isSaveActionLoading || isSaveDestinationsLoading}
            >
              <option value="">Automático (por archivo)</option>
              {presentations.map((presentation: any) => (
                <option key={presentation.id} value={presentation.id}>
                  {presentation.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isSaveActionLoading}>Cancelar</Button>
          <Button onClick={handleConfirmSave} data-testid="save-report-confirm" disabled={isSaveActionLoading}>
            {isSaveActionLoading ? "Guardando..." : "Guardar Reporte"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
