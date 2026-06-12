// lib/state.ts
import { atom } from 'jotai';

function normalizeFilterStateValue(value: unknown): unknown {
  if (typeof value === 'string') {
    const normalized = value.replace(/\0/g, '').normalize('NFC').replace(/\s+/g, ' ').trim();
    return normalized;
  }

  return value;
}

function areFilterRecordsEquivalent(
  left: Record<string, any>,
  right: Record<string, any>
): boolean {
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();

  if (leftKeys.length !== rightKeys.length) return false;

  for (let index = 0; index < leftKeys.length; index += 1) {
    if (leftKeys[index] !== rightKeys[index]) {
      return false;
    }
  }

  return leftKeys.every((key) => {
    const leftValue = normalizeFilterStateValue(left[key]);
    const rightValue = normalizeFilterStateValue(right[key]);
    return Object.is(leftValue, rightValue);
  });
}

export const filtersAtom = atom<Record<string, string | null>>({});

// 🦆 [FASE 4] Cross-Filter State
export const crossFilterAtom = atom<Record<string, string | null>>({});
export const duckdbReadyAtom = atom<boolean>(false);

// 🚀 [PERF] DrillDown State — aislado en atom para evitar re-render de ChatInterface
export interface DrillDownState {
  isVisible: boolean;
  position: { x: number; y: number };
  dataContext: {
    category: string;
    value: number | string;
    series: string;
    tableName?: string;
    secondaryCategory?: string;
    crossFilterContext?: any;
    option?: any;
  };
}
export const drillDownAtom = atom<DrillDownState>({
  isVisible: false,
  position: { x: 0, y: 0 },
  dataContext: { category: '', value: 0, series: '' }
});

// Derived atom: solo isVisible — ECharts subscribe sin recibir cambios de position/context
export const drillDownVisibleAtom = atom((get) => get(drillDownAtom).isVisible);

// -------------------------------------------------------------
// 🧠 [FASE 5] Global State para la Separación de Responsabilidades
// -------------------------------------------------------------

export interface AnalysisComponent {
  type: "mensaje_resumen" | "metricas_clave" | "tabla_datos" | "configuracion_echarts" | "smart_table" | "recomendaciones" | "error" | "correlaciones" | "explicabilidad_analitica";
  content?: string;
  data?: any;
  option?: any;
  items?: string[];
  title?: string;
  texto?: string;              // Para leer el resumen nuevo
  metricas_destacadas?: any;   // Para leer los KPIs ocultos
  puntos_clave?: string[];     // Para leer las alertas amarillas
  // 📋 [FASE 2] Smart Table properties
  columns?: any[];             // Definición de columnas Smart Table
  sort_by?: string;            // Columna de ordenamiento por defecto
  sort_order?: "asc" | "desc"; // Dirección de ordenamiento
  original_chart_option?: any; // ECharts option original para toggle tabla↔gráfico
  default_view_mode?: "table" | "chart" | "hybrid";
  table_name?: string;         // [FASE 4 Fix] Nombre de la tabla aislada en DuckDB
}

export interface WorkspaceRenderState {
  status: "idle" | "analyzing" | "staging";
  message: string | null;
  pendingVisuals: number;
  renderedVisuals: number;
}

// Atom donde viven los componentes pesados para el lienzo central (Ephemerals)
export const workspaceItemsAtom = atom<AnalysisComponent[]>([]);
export const workspaceRenderStateAtom = atom<WorkspaceRenderState>({
  status: "idle",
  message: null,
  pendingVisuals: 0,
  renderedVisuals: 0,
});

// -------------------------------------------------------------
// 🧠 [FASE 5.3 & 5.4] Global Presentation State (Zustand/Jotai)
// -------------------------------------------------------------

export interface SavedReport {
  id: string;
  title: string;
  content: any;
  created_at: string;
  type?: string;
  file_id: string; 
}

export interface Presentation {
  id: string;
  name: string;
  file_id: string;
  created_at: string;
}

export interface PresentationState {
  activePresentationId: string | null;
  activeFileId: string | null;
  globalFilters: Record<string, any>;
  presentations: Presentation[];
  widgets: SavedReport[];
}

export const presentationStateAtom = atom<PresentationState>({
  activePresentationId: null,
  activeFileId: null,
  globalFilters: {},
  presentations: [],
  widgets: []
});

// Selectors
export const activePresentationIdAtom = atom(
  (get) => get(presentationStateAtom).activePresentationId,
  (get, set, newId: string | null) => {
    const state = get(presentationStateAtom);
    const selectedPres = state.presentations.find(p => p.id === newId);
    
    set(presentationStateAtom, { 
      ...state, 
      activePresentationId: newId,
      // Auto-switch data scope when presentation changes
      activeFileId: selectedPres ? selectedPres.file_id : (newId ? state.activeFileId : null)
    });
  }
);

export const presentationsListAtom = atom(
  (get) => get(presentationStateAtom).presentations,
  (get, set, list: Presentation[]) => {
    set(presentationStateAtom, { ...get(presentationStateAtom), presentations: list });
  }
);

export const activeFileIdAtom = atom(
  (get) => get(presentationStateAtom).activeFileId,
  (get, set, newFileId: string | null) => {
    set(presentationStateAtom, { ...get(presentationStateAtom), activeFileId: newFileId });
  }
);

export const globalFiltersAtom = atom(
  (get) => get(presentationStateAtom).globalFilters,
  (get, set, newFilters: Record<string, any>) => {
    const state = get(presentationStateAtom);
    const currentFilters = state.globalFilters || {};
    const nextFilters = newFilters || {};

    if (areFilterRecordsEquivalent(currentFilters, nextFilters)) {
      return;
    }

    set(presentationStateAtom, { ...state, globalFilters: nextFilters });
  }
);

export const presentationWidgetsAtom = atom(
  (get) => get(presentationStateAtom).widgets,
  (get, set, newWidgets: SavedReport[]) => {
    set(presentationStateAtom, { ...get(presentationStateAtom), widgets: newWidgets });
  }
);
