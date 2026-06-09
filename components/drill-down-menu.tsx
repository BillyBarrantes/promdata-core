"use client";

import React, { useState, useMemo, useEffect } from 'react';
import { Card } from "@/components/ui/card";
import { Sparkles, ArrowRight, TrendingUp, Search, Scale, RefreshCw, Target, PieChart, BarChart2, Zap } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";

export interface DrillDownOption {
    id: string;
    label: string;
    icon: React.ReactNode;
    promptTemplate: string;
}

interface DrillDownMenuProps {
    isVisible: boolean;
    position: { x: number; y: number };
    dataContext: {
        category: string;
        value: number | string;
        series: string;
        tableName?: string;
        secondaryCategory?: string;
    };
    onSelect: (prompt: string) => void;
    onClose: () => void;
    // 🦆 [FASE 4] Cross-filter support (Multidimensional)
    isDuckDBReady?: boolean;
    onCrossFilter?: (filters: Record<string, string>, tableName?: string) => void;
}

export function DrillDownMenu({ isVisible, position, dataContext, onSelect, onClose, isDuckDBReady, onCrossFilter }: DrillDownMenuProps) {
    const [seed, setSeed] = useState(0);
    const [menuStyle, setMenuStyle] = useState<React.CSSProperties>({});

    // Pool de Opciones Ampliado
    const allOptions: DrillDownOption[] = useMemo(() => [
        {
            id: 'causes',
            label: 'Causas Raíz',
            icon: <Search className="w-4 h-4 text-blue-500" />,
            promptTemplate: `🔍 Drill-Down: Analiza las **causas raíz** del desempeño de "${dataContext.category}" en "${dataContext.series}" (Valor: ${dataContext.value}). ¿Qué factores específicos impulsan esto?`
        },
        {
            id: 'trends',
            label: 'Tendencia Histórica',
            icon: <TrendingUp className="w-4 h-4 text-green-500" />,
            promptTemplate: `📈 Drill-Down: Muestra la **tendencia histórica** de "${dataContext.category}". ¿El valor actual es una anomalía o sigue un patrón?`
        },
        {
            id: 'compare',
            label: 'Comparar Mercado',
            icon: <Scale className="w-4 h-4 text-purple-500" />,
            promptTemplate: `⚖️ Drill-Down: Compara "${dataContext.category}" (Valor: ${dataContext.value}) con el **promedio del mercado**. ¿Rendimiento superior o inferior?`
        },
        {
            id: 'composition',
            label: 'Composición Interna',
            icon: <PieChart className="w-4 h-4 text-orange-500" />,
            promptTemplate: `🍰 Drill-Down: Desglosa la composición de "${dataContext.category}". ¿Qué sub-elementos lo conforman principalmente?`
        },
        {
            id: 'forecast',
            label: 'Proyección Futura',
            icon: <Target className="w-4 h-4 text-red-500" />,
            promptTemplate: `🔮 Drill-Down: Realiza una proyección para "${dataContext.category}" basada en "${dataContext.series}". ¿Qué se espera para el próximo periodo?`
        },
        {
            id: 'correlation',
            label: 'Correlaciones',
            icon: <BarChart2 className="w-4 h-4 text-indigo-500" />,
            promptTemplate: `🔗 Drill-Down: ¿Con qué otras variables se correlaciona "${dataContext.category}"? Busca relaciones ocultas.`
        }
    ], [dataContext]);

    // Seleccionar 3 opciones aleatorias basadas en 'seed'
    const displayedOptions = useMemo(() => {
        // Simple shuffle determinístico basado en seed
        const shuffled = [...allOptions].sort(() => 0.5 - Math.random());
        return shuffled.slice(0, 3);
    }, [allOptions, seed]);

    const handleRefresh = (e: React.MouseEvent) => {
        e.stopPropagation();
        setSeed(prev => prev + 1); // Forzar re-calculo
    };

    // Ajuste de Posición Inteligente (Evitar salir de pantalla)
    useEffect(() => {
        if (!isVisible) return;

        const isRightSide = position.x > (window.innerWidth / 2);
        const isBottomSide = position.y > (window.innerHeight - 300); // 300px altura aprox menú

        setMenuStyle({
            position: 'fixed',
            left: position.x,
            top: position.y,
            zIndex: 9999,
            // Si está a la derecha, mover el menú a la izquierda del cursor (-100% width)
            // Si está abajo, subirlo (-100% height)
            transform: `translate(${isRightSide ? 'calc(-100% - 15px)' : '15px'}, ${isBottomSide ? '-100%' : '-10%'})`,
        });
    }, [isVisible, position]);

    if (!isVisible) return null;

    return (
        <>
            {/* Overlay transparente para cerrar */}
            <div
                className="fixed inset-0 z-[9998]"
                onClick={onClose}
                style={{ background: 'transparent', cursor: 'default' }}
            />
            <AnimatePresence>
                {isVisible && (
                    <motion.div
                        key="drill-menu"
                        initial={{ opacity: 0, scale: 0.95 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.95 }}
                        transition={{ duration: 0.15, ease: "easeOut" }}
                        style={menuStyle}
                        className="flex flex-col"
                    >
                        <Card className="shadow-2xl border-primary/20 bg-card/95 backdrop-blur-md w-[260px] overflow-hidden rounded-xl ring-1 ring-black/5">
                            {/* Header */}
                            <div className="p-2.5 border-b bg-muted/40 flex justify-between items-center">
                                <div className="flex items-center gap-2">
                                    <Sparkles className="w-3.5 h-3.5 text-yellow-500 fill-yellow-500" />
                                    <span className="text-[11px] font-bold text-muted-foreground uppercase tracking-widest">
                                        Sugerencias de Análisis
                                    </span>
                                </div>
                                <div className="flex gap-1">
                                    <button
                                        onClick={handleRefresh}
                                        className="p-1 hover:bg-background rounded-md text-muted-foreground hover:text-primary transition-colors"
                                        title="Nuevas sugerencias"
                                    >
                                        <RefreshCw className="w-3.5 h-3.5" />
                                    </button>
                                    <button onClick={onClose} className="p-1 hover:bg-background rounded-md text-muted-foreground hover:text-destructive transition-colors">
                                        <span className="text-xs font-bold leading-none">✕</span>
                                    </button>
                                </div>
                            </div>

                            {/* Content */}
                            <div className="p-2 flex flex-col gap-1">
                                <div className="px-2 py-1 flex items-center justify-between text-xs font-medium text-foreground border-b border-border/50 mb-1 pb-1.5">
                                    <span>Foco: <span className="text-primary truncate max-w-[120px] inline-block align-bottom">{dataContext.category}</span></span>
                                    <span className="text-muted-foreground font-mono">{dataContext.value}</span>
                                </div>

                                <div className="flex flex-col gap-1">
                                    {/* 🦆 [FASE 4] Cross-Filter Instantáneo */}
                                    {onCrossFilter && (
                                        <button
                                            onClick={() => {
                                                const filters: Record<string, string> = {};

                                                if (dataContext.category && dataContext.category !== 'undefined') {
                                                    filters['global_chart_filter'] = String(dataContext.category);
                                                }

                                                if (
                                                    dataContext.secondaryCategory &&
                                                    dataContext.secondaryCategory !== 'undefined' &&
                                                    dataContext.secondaryCategory !== dataContext.category
                                                ) {
                                                    filters['global_cross_filter'] = String(dataContext.secondaryCategory);
                                                }

                                                // [FIX 2026-06-08] Preview los filtros base del chart original
                                                // (los que el canary executor aplicó para generar este chart).
                                                // El usuario debe ver explícitamente que estos se combinarán
                                                // con su clic para producir la tabla resultante.
                                                const chartOption = (dataContext as any)?.option;
                                                const baseFilters = chartOption?.chart_base_filters || {};
                                                const baseFilterKeys = Object.keys(baseFilters);
                                                const baseFilterHint = baseFilterKeys.length > 0
                                                    ? `+ ${baseFilterKeys.length} base`
                                                    : '';

                                                console.log("1. Iniciando filtro. Tabla:", dataContext.tableName, "Filtros:", filters, "Base filters:", baseFilters);
                                                onCrossFilter(filters, dataContext.tableName);
                                                onClose();
                                            }}
                                            className="flex items-center gap-2.5 w-full p-2 rounded-lg bg-primary/5 hover:bg-primary/15 transition-all text-left group border border-primary/20 hover:border-primary/40 relative overflow-hidden mb-1"
                                        >
                                            <div className="p-1.5 rounded-md bg-primary/10 shadow-sm border border-primary/20 flex items-center justify-center shrink-0">
                                                <Zap className="w-4 h-4 text-primary fill-primary/30" />
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <p className="text-[13px] font-semibold text-primary truncate">⚡ Filtrar aquí</p>
                                                <p className="text-[10px] text-muted-foreground">
                                                    Instantáneo · Sin servidor
                                                    {/* Show base filter count if any exist */}
                                                    <BaseFilterBadge dataContext={dataContext} />
                                                </p>
                                            </div>
                                        </button>
                                    )}
                                    {displayedOptions.map((opt) => (
                                        <motion.button
                                            key={`${opt.id}-${seed}`}
                                            initial={{ opacity: 0, x: -10 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ duration: 0.2 }}
                                            onClick={() => onSelect(opt.promptTemplate)}
                                            className="flex items-center gap-2.5 w-full p-2 rounded-lg hover:bg-accent/80 transition-all text-left group border border-transparent hover:border-primary/10 relative overflow-hidden"
                                        >
                                            <div className={cn(
                                                "p-1.5 rounded-md bg-background shadow-sm border group-hover:bg-card transition-colors shrink-0",
                                                "flex items-center justify-center"
                                            )}>
                                                {opt.icon}
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <p className="text-[13px] font-medium text-foreground group-hover:text-primary transition-colors truncate">
                                                    {opt.label}
                                                </p>
                                            </div>
                                            <ArrowRight className="w-3 h-3 text-muted-foreground opacity-0 group-hover:opacity-100 transition-all transform group-hover:-translate-x-1 shrink-0" />
                                        </motion.button>
                                    ))}
                                </div>
                            </div>
                        </Card>
                    </motion.div>
                )}
            </AnimatePresence>
        </>
    );
}

/**
 * [FIX 2026-06-08] Muestra un badge con el conteo de filtros base del chart
 * (los que el canary executor aplicó al generar el chart, ej.
 * "Tipo Movimiento = Ingreso"). El usuario ve explícitamente que el filtro
 * del clic se va a COMBINAR con N filtros base del chart, no reemplazarlos.
 */
function BaseFilterBadge({ dataContext }: { dataContext: any }) {
    const chartOption = dataContext?.option;
    const baseFilters = chartOption?.chart_base_filters;
    if (!baseFilters || typeof baseFilters !== "object" || Object.keys(baseFilters).length === 0) {
        return null;
    }
    const count = Object.keys(baseFilters).length;
    return (
        <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold bg-amber-100 text-amber-700 border border-amber-200">
            + {count} base
        </span>
    );
}
