"use client"

import { useState, useEffect } from "react"
import { useSupabase } from "@/lib/supabase-provider"
import { Sidebar } from "@/components/sidebar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Plus, Pencil, Trash2, BookOpen, Search } from "lucide-react"
import { toast } from "sonner"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"

interface GlossaryTerm {
    id: string
    term: string
    definition: string
    created_at: string
}

export default function GlosarioPage() {
    const supabase = useSupabase()
    const [terms, setTerms] = useState<GlossaryTerm[]>([])
    const [loading, setLoading] = useState(true)
    const [searchTerm, setSearchTerm] = useState("")

    // Modal State
    const [isDialogOpen, setIsDialogOpen] = useState(false)
    const [editingTerm, setEditingTerm] = useState<GlossaryTerm | null>(null)

    // Form State
    const [formData, setFormData] = useState({ term: "", definition: "" })

    const fetchTerms = async () => {
        if (!supabase) return
        try {
            setLoading(true)
            const { data, error } = await supabase
                .from('business_glossary')
                .select('*')
                .order('term', { ascending: true })

            if (error) throw error
            setTerms(data || [])
        } catch (error: any) {
            toast.error("Error al cargar términos: " + error.message)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        fetchTerms()
    }, [supabase])

    const handleSave = async () => {
        if (!formData.term.trim() || !formData.definition.trim()) {
            toast.error("Completa ambos campos")
            return
        }

        try {
            const { data: { session } } = await supabase.auth.getSession()
            if (!session) return

            const payload = {
                user_id: session.user.id,
                term: formData.term.trim(),
                definition: formData.definition.trim()
            }

            let error
            if (editingTerm) {
                // Update
                const { error: updateError } = await supabase
                    .from('business_glossary')
                    .update(payload)
                    .eq('id', editingTerm.id)
                error = updateError
            } else {
                // Insert
                const { error: insertError } = await supabase
                    .from('business_glossary')
                    .insert([payload])
                error = insertError
            }

            if (error) {
                if (error.code === '23505') { // Unique violation
                    toast.error("Este término ya existe en tu glosario.")
                } else {
                    throw error
                }
                return
            }

            toast.success(editingTerm ? "Término actualizado" : "Término agregado")
            setIsDialogOpen(false)
            fetchTerms()
        } catch (e: any) {
            console.error(e)
            toast.error("Error al guardar: " + e.message)
        }
    }

    const handleDelete = async (id: string) => {
        try {
            const { error } = await supabase
                .from('business_glossary')
                .delete()
                .eq('id', id)

            if (error) throw error
            toast.success("Término eliminado")
            setTerms(prev => prev.filter(t => t.id !== id))
        } catch (e: any) {
            toast.error("Error al eliminar: " + e.message)
        }
    }

    const openNew = () => {
        setEditingTerm(null)
        setFormData({ term: "", definition: "" })
        setIsDialogOpen(true)
    }

    const openEdit = (term: GlossaryTerm) => {
        setEditingTerm(term)
        setFormData({ term: term.term, definition: term.definition })
        setIsDialogOpen(true)
    }

    const filteredTerms = terms.filter(t =>
        t.term.toLowerCase().includes(searchTerm.toLowerCase()) ||
        t.definition.toLowerCase().includes(searchTerm.toLowerCase())
    )

    return (
        <div className="flex h-screen bg-background">
            <Sidebar />
            <main className="flex-1 flex flex-col overflow-hidden">
                <div className="flex-1 overflow-y-auto p-8">
                    <div className="max-w-5xl mx-auto w-full space-y-8">

                        {/* Header */}
                        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 py-4">
                            <div>
                                <h1 className="text-4xl font-normal tracking-tight flex items-center gap-3 text-foreground">
                                    <BookOpen className="w-8 h-8 text-primary/80" strokeWidth={1.5} />
                                    Glosario de Negocio
                                </h1>
                                <p className="text-muted-foreground mt-2 text-lg font-light">
                                    "Enséñale" a la IA los términos clave de tu empresa para análisis más precisos.
                                </p>
                            </div>
                            <Button onClick={openNew} className="shrink-0 gap-2">
                                <Plus className="w-4 h-4" />
                                Agregar Término
                            </Button>
                        </div>

                        {/* Search & List */}
                        <Card>
                            <CardHeader className="pb-3 border-b">
                                <div className="relative">
                                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                                    <Input
                                        placeholder="Buscar término o definición..."
                                        className="pl-10 h-12 rounded-2xl bg-muted/30 border-transparent focus:bg-background transition-all"
                                        value={searchTerm}
                                        onChange={(e) => setSearchTerm(e.target.value)}
                                    />
                                </div>
                            </CardHeader>
                            <CardContent className="p-0">
                                {loading ? (
                                    <div className="p-8 text-center text-muted-foreground">Cargando glosario...</div>
                                ) : filteredTerms.length === 0 ? (
                                    <div className="p-8">
                                        <div className="text-center mb-8">
                                            <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center mx-auto mb-3">
                                                <BookOpen className="w-6 h-6 text-muted-foreground" />
                                            </div>
                                            <h3 className="font-medium text-lg">Tu glosario está vacío</h3>
                                            <p className="text-muted-foreground">
                                                Empieza con estos ejemplos para guiarte:
                                            </p>
                                        </div>

                                        <div className="grid md:grid-cols-2 gap-6 max-w-3xl mx-auto mb-8">
                                            {/* Ejemplo Básico */}
                                            <div className="border border-border/60 rounded-[1.5rem] p-6 bg-card hover:shadow-lg transition-all duration-300">
                                                <div className="flex items-center gap-2 mb-3">
                                                    <span className="bg-blue-100 text-blue-700 text-xs font-bold px-2 py-0.5 rounded border border-blue-200">Básico</span>
                                                    <span className="font-semibold text-sm">Identificadores</span>
                                                </div>
                                                <div className="space-y-2 text-sm">
                                                    <p><span className="font-mono text-muted-foreground">Término:</span> <strong>cod_z</strong></p>
                                                    <p><span className="font-mono text-muted-foreground">Definición:</span> Código interno para referirse a las 'Zapatillas Urbanas' de la temporada 2024.</p>
                                                </div>
                                            </div>

                                            {/* Ejemplo Profesional */}
                                            <div className="border border-border/60 rounded-[1.5rem] p-6 bg-card hover:shadow-lg transition-all duration-300">
                                                <div className="flex items-center gap-2 mb-3">
                                                    <span className="bg-emerald-100 text-emerald-700 text-xs font-bold px-2 py-0.5 rounded border border-emerald-200">Profesional</span>
                                                    <span className="font-semibold text-sm">Métrica Financiera</span>
                                                </div>
                                                <div className="space-y-2 text-sm">
                                                    <p><span className="font-mono text-muted-foreground">Término:</span> <strong>Margen Contributivo 2 (MC2)</strong></p>
                                                    <p><span className="font-mono text-muted-foreground">Definición:</span> Resultado de (Ventas Netas - Costo de Ventas - Gastos Variables Directos). No incluir gastos administrativos ni alquileres en este cálculo.</p>
                                                </div>
                                            </div>
                                        </div>

                                        <div className="flex justify-center">
                                            <Button variant="outline" onClick={openNew}>Crear primer término</Button>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="rounded-md border-t-0">
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead className="w-[200px]">Término</TableHead>
                                                    <TableHead>Definición de Negocio</TableHead>
                                                    <TableHead className="w-[100px] text-right">Acciones</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {filteredTerms.map((term) => (
                                                    <TableRow key={term.id}>
                                                        <TableCell className="font-semibold text-primary">{term.term}</TableCell>
                                                        <TableCell className="text-muted-foreground">{term.definition}</TableCell>
                                                        <TableCell className="text-right">
                                                            <div className="flex items-center justify-end gap-2">
                                                                <Button size="icon" variant="ghost" className="h-8 w-8 text-muted-foreground hover:text-primary" onClick={() => openEdit(term)}>
                                                                    <Pencil className="w-4 h-4" />
                                                                </Button>
                                                                <Button size="icon" variant="ghost" className="h-8 w-8 text-muted-foreground hover:text-destructive" onClick={() => handleDelete(term.id)}>
                                                                    <Trash2 className="w-4 h-4" />
                                                                </Button>
                                                            </div>
                                                        </TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                    </div>
                                )}
                            </CardContent>
                        </Card>

                        {/* Dialog Form */}
                        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                            <DialogContent className="sm:max-w-[500px]">
                                <DialogHeader>
                                    <DialogTitle>{editingTerm ? "Editar Término" : "Nuevo Término"}</DialogTitle>
                                    <DialogDescription>
                                        La IA recordará esto para futuros análisis.
                                    </DialogDescription>
                                </DialogHeader>
                                <div className="grid gap-4 py-4">
                                    <div className="grid gap-2">
                                        <label htmlFor="term" className="text-sm font-medium">Nombre del Término</label>
                                        <Input
                                            id="term"
                                            value={formData.term}
                                            onChange={(e) => setFormData(prev => ({ ...prev, term: e.target.value }))}
                                            placeholder="Ej: Margen Operativo Real"
                                        />
                                    </div>
                                    <div className="grid gap-2">
                                        <label htmlFor="def" className="text-sm font-medium">Definición / Contexto</label>
                                        <Textarea
                                            id="def"
                                            value={formData.definition}
                                            onChange={(e) => setFormData(prev => ({ ...prev, definition: e.target.value }))}
                                            placeholder="Ej: Se calcula restando los costos variables y fijos directos, excluyendo depreciación..."
                                            rows={4}
                                        />
                                    </div>
                                </div>
                                <DialogFooter>
                                    <Button variant="outline" onClick={() => setIsDialogOpen(false)}>Cancelar</Button>
                                    <Button onClick={handleSave}>Guardar Término</Button>
                                </DialogFooter>
                            </DialogContent>
                        </Dialog>

                    </div>
                </div>
            </main>
        </div>
    )
}
