"use client";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Sidebar } from '@/components/sidebar';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

import { useSupabase } from '@/lib/supabase-provider';
import { toast } from 'sonner';
import dynamic from 'next/dynamic';
import { Copy, FileText, Library, Loader2, Pencil, Plus, RefreshCw, Presentation, Trash2 } from 'lucide-react';
import { GridWidget } from '@/components/dashboard/grid-widget';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import './dashboard-grid.css';
import { useAtom } from 'jotai';
import { duckdbReadyAtom, SavedReport, activePresentationIdAtom, presentationsListAtom, Presentation as PresentationModel, globalFiltersAtom } from '@/lib/state';
import * as duckdbEngine from '@/lib/duckdb-engine';
import {
  buildExecutiveWidgetSnapshot,
  ExecutiveNarrativeWidgetSnapshot,
  normalizeExecutiveFilters,
} from '@/lib/dashboard-narrative';
import { exportDashboardAsImage, exportDashboardAsPdf } from '@/lib/dashboard-export';
import { getScopedLocalPerfAverage } from '@/lib/local-performance';

const ResponsiveGridLayout = dynamic(
  () => import('react-grid-layout/legacy').then(mod => (mod as any).WidthProvider((mod as any).Responsive)),
  { ssr: false, loading: () => <div className="p-8 text-center text-muted-foreground animate-pulse">Cargando Tablero Interactivo...</div> }
) as any; // Cast: dynamic() devuelve ComponentType<{}>, el grid necesita props tipadas en runtime

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const collectDashboardPreloads = (reports: SavedReport[]): duckdbEngine.ArrowPreloadEntry[] => {
  const rawEntries = reports.flatMap((report, index) => {
    const content = report?.content || {};
    const innerContent = content?.content || {};
    const chartTableName = `dashboard_widget_${report.id.replace(/-/g, '_')}`;

    if (content?.type === 'chart') {
      const arrowPayload = innerContent?.granular_arrow || innerContent?.original_chart_option?.granular_arrow || null;
      return arrowPayload ? [{ tableName: chartTableName, base64Data: arrowPayload, priority: index }] : [];
    }

    if (content?.type === 'table') {
      const arrowPayload = innerContent?.granular_arrow || innerContent?.original_chart_option?.granular_arrow || null;
      return arrowPayload ? [{ tableName: chartTableName, base64Data: arrowPayload, priority: index + 1 }] : [];
    }

    return [];
  });

  return rawEntries.sort((left, right) => {
    const leftHistoricalCost = getScopedLocalPerfAverage('duckdb_cross_filter', left.tableName) || 0;
    const rightHistoricalCost = getScopedLocalPerfAverage('duckdb_cross_filter', right.tableName) || 0;

    if (leftHistoricalCost !== rightHistoricalCost) {
      return rightHistoricalCost - leftHistoricalCost;
    }

    return (left.priority ?? 99) - (right.priority ?? 99);
  });
};

const extractRawChartCategory = (params: any): string | null => {
  const candidates = [
    params?.rawCategory,
    params?.data?.raw_name,
    params?.data?.rawName,
    params?.data?.full_name,
    params?.data?.fullName,
    params?.data?.name,
    params?.name,
    params?.axisValue,
    params?.axisValueLabel,
    params?.data?.value?.[3],
    params?.value?.[3],
  ];

  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.replace(/\0/g, "").normalize('NFC').replace(/\s+/g, ' ').trim();
    }
  }

  return null;
};

const normalizeGlobalFilterValue = (value: string) =>
  value.replace(/\0/g, '').normalize('NFC').replace(/\s+/g, ' ').trim();

const canonicalizeFilterKey = (value: string) =>
  normalizeGlobalFilterValue(value)
    .toLowerCase()
    .replace(/\u2026/g, '...')
    .replace(/\.{3}$/g, '')
    .replace(/\s+/g, ' ')
    .trim();

const areEquivalentFilterValues = (left: string, right: string): boolean => {
  if (!left || !right) return false;
  if (left === right) return true;

  const normalizedLeft = canonicalizeFilterKey(left);
  const normalizedRight = canonicalizeFilterKey(right);
  if (!normalizedLeft || !normalizedRight) return false;
  if (normalizedLeft === normalizedRight) return true;

  const minLength = Math.min(normalizedLeft.length, normalizedRight.length);
  if (minLength >= 12 && (normalizedLeft.startsWith(normalizedRight) || normalizedRight.startsWith(normalizedLeft))) {
    return true;
  }

  return false;
};

const getPresentationCacheKey = (presentationId: string | null | undefined): string =>
  presentationId && presentationId.trim() ? presentationId.trim() : '__legacy__';

const buildLayoutMap = (newLayout: any[]): Record<string, { x: number; y: number; w: number; h: number }> => {
  const layoutMap: Record<string, { x: number; y: number; w: number; h: number }> = {};
  newLayout.forEach((layoutItem) => {
    layoutMap[layoutItem.i] = {
      x: layoutItem.x,
      y: layoutItem.y,
      w: layoutItem.w,
      h: layoutItem.h,
    };
  });
  return layoutMap;
};

const areLayoutMapsEqual = (
  left: Record<string, { x: number; y: number; w: number; h: number }>,
  right: Record<string, { x: number; y: number; w: number; h: number }>
) => {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;

  for (const key of leftKeys) {
    const leftLayout = left[key];
    const rightLayout = right[key];
    if (!rightLayout) return false;
    if (
      leftLayout.x !== rightLayout.x ||
      leftLayout.y !== rightLayout.y ||
      leftLayout.w !== rightLayout.w ||
      leftLayout.h !== rightLayout.h
    ) {
      return false;
    }
  }

  return true;
};

interface DashboardExecutiveSummary {
  headline: string;
  overview: string;
  key_findings: string[];
  risks: string[];
  actions: string[];
  caveats: string[];
  widget_count: number;
  mixed_sources: boolean;
  filter_scope: string[];
}

function DashboardPageClient() {
  const [mounted, setMounted] = useState(false);
  const [reports, setReports] = useState<SavedReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [isDeletePresentationOpen, setIsDeletePresentationOpen] = useState(false);
  const [isCreatePresentationOpen, setIsCreatePresentationOpen] = useState(false);
  const [isRenamePresentationOpen, setIsRenamePresentationOpen] = useState(false);
  const [isDuplicatePresentationOpen, setIsDuplicatePresentationOpen] = useState(false);
  const [isLibraryOpen, setIsLibraryOpen] = useState(false);
  const [isExecutiveSummaryOpen, setIsExecutiveSummaryOpen] = useState(false);
  const [presentationMode, setPresentationMode] = useState(false);
  const [presentationSummaryVisible, setPresentationSummaryVisible] = useState(false);
  const [focusedReportId, setFocusedReportId] = useState<string | null>(null);
  const [createPresentationName, setCreatePresentationName] = useState("");
  const [renamePresentationName, setRenamePresentationName] = useState("");
  const [duplicatePresentationName, setDuplicatePresentationName] = useState("");
  const [isPresentationActionLoading, setIsPresentationActionLoading] = useState(false);
  const [librarySearch, setLibrarySearch] = useState("");
  const [selectedLibraryPresentationId, setSelectedLibraryPresentationId] = useState("");
  const [libraryReports, setLibraryReports] = useState<SavedReport[]>([]);
  const [isLibraryLoading, setIsLibraryLoading] = useState(false);
  const [injectingLibraryReportId, setInjectingLibraryReportId] = useState<string | null>(null);
  const [isExecutiveSummaryLoading, setIsExecutiveSummaryLoading] = useState(false);
  const [isPresentationExporting, setIsPresentationExporting] = useState<"png" | "jpg" | "pdf" | null>(null);
  const [executiveSummary, setExecutiveSummary] = useState<DashboardExecutiveSummary | null>(null);
  const [layoutOverrides, setLayoutOverrides] = useState<Record<string, { x: number; y: number; w: number; h: number }>>({});
  const [isDuckDBReady, setIsDuckDBReady] = useAtom(duckdbReadyAtom);
  const [activePresentationId, setActivePresentationId] = useAtom(activePresentationIdAtom);
  const [presentations, setPresentations] = useAtom(presentationsListAtom);
  const [globalFilters, setGlobalFilters] = useAtom(globalFiltersAtom);
  const layoutSaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingLayoutRef = useRef<Record<string, { x: number; y: number; w: number; h: number }>>({});
  const layoutPersistRequestVersionRef = useRef(0);
  const activePresentationIdRef = useRef<string | null>(activePresentationId);
  const reportsCacheRef = useRef<Record<string, SavedReport[]>>({});
  const widgetSnapshotsRef = useRef<Record<string, ExecutiveNarrativeWidgetSnapshot>>({});
  const presentationShellRef = useRef<HTMLDivElement | null>(null);
  const presentationRootRef = useRef<HTMLDivElement | null>(null);

  const supabase = useSupabase();
  const router = useRouter();
  const searchParams = useSearchParams();
  const safePresentations = Array.isArray(presentations) ? presentations : [];
  const activePresentation = safePresentations.find((presentation) => presentation.id === activePresentationId) || null;
  const requestedPresentationId = useMemo(() => {
    const rawValue = searchParams.get('presentationId');
    return rawValue && rawValue.trim() ? rawValue.trim() : null;
  }, [searchParams]);
  const getDashboardAccessToken = useCallback(async (): Promise<string | null> => {
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) return session.access_token;

    if (typeof window !== 'undefined' && process.env.NODE_ENV !== 'production') {
      const params = new URLSearchParams(window.location.search);
      if (params.get('__qa_dashboard') === '1') {
        return params.get('__qa_dashboard_token') || 'qa-dashboard-token';
      }
    }

    return null;
  }, [supabase]);

  const normalizeReportsWithLayout = (rawReports: any[]): SavedReport[] => {
    return (Array.isArray(rawReports) ? rawReports : []).map((report: any) => {
      const content = report?.content && typeof report.content === 'object' ? report.content : {};
      const layoutFromContent = content?.layout && typeof content.layout === 'object' ? content.layout : {};

      const normalizedLayout = {
        x: report?.layout_x ?? layoutFromContent?.x ?? null,
        y: report?.layout_y ?? layoutFromContent?.y ?? null,
        w: report?.layout_w ?? layoutFromContent?.w ?? null,
        h: report?.layout_h ?? layoutFromContent?.h ?? null,
      };

      const hasPersistedLayout = Object.values(normalizedLayout).every((value) => value !== null && value !== undefined);

      return {
        ...report,
        content: {
          ...content,
          layout: hasPersistedLayout ? normalizedLayout : layoutFromContent
        }
      };
    });
  };

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    let cancelled = false;

    const scheduleWarmup = () => {
      duckdbEngine
        .warmup()
        .then(() => {
          if (!cancelled) {
            setIsDuckDBReady(true);
          }
        })
        .catch((error: any) => {
          console.warn('⚠️ [DuckDB] Warm-up dashboard no completado:', error);
        });
    };

    if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
      const idleId = window.requestIdleCallback(() => scheduleWarmup(), { timeout: 1200 });
      return () => {
        cancelled = true;
        window.cancelIdleCallback(idleId);
      };
    }

    const timeoutId = globalThis.setTimeout(scheduleWarmup, 300);
    return () => {
      cancelled = true;
      globalThis.clearTimeout(timeoutId);
    };
  }, [setIsDuckDBReady]);

  useEffect(() => {
    return () => {
      if (layoutSaveTimeoutRef.current) {
        clearTimeout(layoutSaveTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    activePresentationIdRef.current = activePresentationId;
    layoutPersistRequestVersionRef.current += 1;
    pendingLayoutRef.current = {};
    setLayoutOverrides({});
    setExecutiveSummary(null);
    setIsExecutiveSummaryOpen(false);
    setPresentationSummaryVisible(false);
    setFocusedReportId(null);
    const cacheKey = getPresentationCacheKey(activePresentationId);
    const cachedReports = reportsCacheRef.current[cacheKey];
    if (cachedReports && cachedReports.length > 0) {
      setReports(cachedReports);
      setLoading(false);
    } else {
      setReports([]);
      setLoading(true);
    }
    if (layoutSaveTimeoutRef.current) {
      clearTimeout(layoutSaveTimeoutRef.current);
      layoutSaveTimeoutRef.current = null;
    }
    setGlobalFilters({});
  }, [activePresentationId]);

  useEffect(() => {
    const activeReportIds = new Set(reports.map((report) => report.id));
    widgetSnapshotsRef.current = Object.fromEntries(
      Object.entries(widgetSnapshotsRef.current).filter(([reportId]) => activeReportIds.has(reportId))
    );
    setExecutiveSummary(null);
    if (focusedReportId && !activeReportIds.has(focusedReportId)) {
      setFocusedReportId(null);
    }
  }, [focusedReportId, reports, globalFilters]);

  useEffect(() => {
    if (typeof window === 'undefined' || process.env.NODE_ENV === 'production') {
      return;
    }

    const params = new URLSearchParams(window.location.search);
    if (params.get('__qa_dashboard') !== '1') {
      return;
    }

    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ category?: string }>).detail;
      const selectedValue = detail?.category ? normalizeGlobalFilterValue(String(detail.category)) : '';
      if (!selectedValue) return;
      setGlobalFilters({ global_cross_filter: selectedValue });
    };

    window.addEventListener('promdata:qa-dashboard-filter', handler as EventListener);
    return () => {
      window.removeEventListener('promdata:qa-dashboard-filter', handler as EventListener);
    };
  }, [setGlobalFilters]);

  const fetchPresentations = useCallback(async (preferredPresentationId?: string | null) => {
    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return [];
      const res = await fetch(`http://localhost:8000/api/v1/presentations?_t=${Date.now()}`, {
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'Cache-Control': 'no-cache, no-store, must-revalidate'
        },
        cache: 'no-store'
      });
      if (!res.ok) return [];

      const data = await res.json();
      const nextPresentations = Array.isArray(data) ? data : [];
      setPresentations(nextPresentations);

      if (preferredPresentationId && nextPresentations.some((presentation: PresentationModel) => presentation.id === preferredPresentationId)) {
        setActivePresentationId(preferredPresentationId);
      } else if (!activePresentationId && nextPresentations.length > 0) {
        setActivePresentationId(nextPresentations[0].id);
      } else if (activePresentationId && !nextPresentations.some((presentation: PresentationModel) => presentation.id === activePresentationId)) {
        setActivePresentationId(nextPresentations[0]?.id || null);
      }

      return nextPresentations;
    } catch (e) {
      console.error("Error cargando presentaciones", e);
      return [];
    }
  }, [activePresentationId, getDashboardAccessToken, setActivePresentationId, setPresentations]);

  useEffect(() => {
    void fetchPresentations(requestedPresentationId || undefined);
  }, [fetchPresentations, requestedPresentationId]);



  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const targetPresentationId = activePresentationId;
    const targetCacheKey = getPresentationCacheKey(targetPresentationId);

    const fetchReports = async () => {
      setLoading(true);

      try {
        const accessToken = await getDashboardAccessToken();
        if (!accessToken) {
          if (!cancelled) setLoading(false);
          return;
        }

        let url = 'http://localhost:8000/api/v1/reports';
        if (targetPresentationId) url += `?presentation_id=${targetPresentationId}`;

        const response = await fetch(url, {
          headers: { 'Authorization': `Bearer ${accessToken}` },
          signal: controller.signal
        });

        if (!response.ok) {
          const errorPayload = await response.text().catch(() => '');
          console.warn("⚠️ [DASHBOARD] Error cargando reportes", {
            status: response.status,
            activePresentationId: targetPresentationId,
            body: errorPayload?.slice(0, 250)
          });
          if (!cancelled) {
            toast.error("No se pudieron cargar tus reportes guardados.");
          }
          return;
        }

        const data = await response.json();
        const normalizedReports = normalizeReportsWithLayout(data);
        reportsCacheRef.current[targetCacheKey] = normalizedReports;

        if (!cancelled && activePresentationIdRef.current === targetPresentationId) {
          setLayoutOverrides({});
          setReports(normalizedReports);
        }
      } catch (error) {
        if ((error as any)?.name === 'AbortError') return;
        if (!cancelled && activePresentationIdRef.current === targetPresentationId) {
          toast.error("No se pudieron cargar tus reportes guardados.");
          console.error(error);
        }
      } finally {
        if (!cancelled && activePresentationIdRef.current === targetPresentationId) {
          setLoading(false);
        }
      }
    };

    void fetchReports();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [activePresentationId, getDashboardAccessToken]);

  useEffect(() => {
    const activeTableNames = reports.map((report) => `dashboard_widget_${report.id.replace(/-/g, '_')}`);
    void duckdbEngine.cleanupInactiveTables(activeTableNames).catch((error) => {
      console.warn('⚠️ [DASHBOARD] Cleanup de tablas inactivas no completado:', error);
    });
  }, [reports]);

  useEffect(() => {
    if (!reports.length) return;

    const preloadEntries = collectDashboardPreloads(reports.slice(0, 4));
    if (!preloadEntries.length) return;

    void duckdbEngine.preloadArrowTables(preloadEntries, 2)
      .then(() => setIsDuckDBReady(true))
      .catch((error) => {
        console.warn('⚠️ [DASHBOARD] Preload perceptual no completado:', error);
      });
  }, [reports, setIsDuckDBReady]);

  const refreshReports = useCallback(async (options?: { silent?: boolean; presentationId?: string | null }) => {
    const targetPresentationId = options?.presentationId ?? activePresentationId;
    const targetCacheKey = getPresentationCacheKey(targetPresentationId);
    setLoading(true);
    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return;
      let url = 'http://localhost:8000/api/v1/reports';
      if (targetPresentationId) url += `?presentation_id=${targetPresentationId}`;
      const response = await fetch(url, {
        headers: { 'Authorization': `Bearer ${accessToken}` }
      });
      if (!response.ok) throw new Error("Error cargando reportes");
      const data = await response.json();
      const normalizedReports = normalizeReportsWithLayout(data);
      reportsCacheRef.current[targetCacheKey] = normalizedReports;
      setLayoutOverrides({});
      setReports(normalizedReports);
      if (!options?.silent) {
        toast.success("Tablero actualizado");
      }
    } catch (e) {
      if (!options?.silent) {
        toast.error("Error al actualizar");
      }
    } finally {
      setLoading(false);
    }
  }, [activePresentationId, getDashboardAccessToken]);

  const loadLibraryReports = useCallback(async (presentationId?: string | null) => {
    setIsLibraryLoading(true);
    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return;

      let url = 'http://localhost:8000/api/v1/reports';
      if (presentationId && presentationId !== '__all__') {
        url += `?presentation_id=${encodeURIComponent(presentationId)}`;
      }

      const response = await fetch(url, {
        headers: { 'Authorization': `Bearer ${accessToken}` },
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`Error cargando biblioteca (${response.status})`);

      const payload = await response.json();
      const normalized = normalizeReportsWithLayout(payload);
      const visualReports = normalized.filter((report) => {
        const type = report?.content?.type;
        return type === 'chart' || type === 'table';
      });

      setLibraryReports(visualReports);
    } catch (error) {
      console.error("Error cargando biblioteca global", error);
      toast.error("No se pudo cargar la biblioteca global.");
    } finally {
      setIsLibraryLoading(false);
    }
  }, [getDashboardAccessToken]);

  const filteredLibraryReports = useMemo(() => {
    const term = librarySearch.trim().toLowerCase();
    if (!term) return libraryReports;

    return libraryReports.filter((report) => {
      const title = typeof report.title === 'string' ? report.title.toLowerCase() : '';
      const fileId = typeof report.file_id === 'string' ? report.file_id.toLowerCase() : '';
      return title.includes(term) || fileId.includes(term);
    });
  }, [libraryReports, librarySearch]);

  const handleOpenLibrary = useCallback(() => {
    const initialPresentationId = activePresentationId || safePresentations[0]?.id || '';
    setSelectedLibraryPresentationId(initialPresentationId);
    setLibrarySearch("");
    setIsLibraryOpen(true);
    if (initialPresentationId) {
      void loadLibraryReports(initialPresentationId);
    } else {
      setLibraryReports([]);
    }
  }, [activePresentationId, loadLibraryReports, safePresentations]);

  const handleLibraryPresentationChange = useCallback((presentationId: string) => {
    setSelectedLibraryPresentationId(presentationId);
    setLibrarySearch("");

    if (!presentationId) {
      setLibraryReports([]);
      setIsLibraryLoading(false);
      return;
    }

    void loadLibraryReports(presentationId);
  }, [loadLibraryReports]);

  const presentationNameById = useMemo(() => {
    return new Map((safePresentations || []).map((presentation) => [presentation.id, presentation.name]));
  }, [safePresentations]);

  const exportBaseFileName = useMemo(() => {
    const rawName = (activePresentation?.name || 'dashboard-ejecutivo')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9-_]+/gi, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '');
    return rawName || 'dashboard-ejecutivo';
  }, [activePresentation?.name]);

  const handleNarrativeSnapshotChange = useCallback((reportId: string, snapshot: ExecutiveNarrativeWidgetSnapshot) => {
    widgetSnapshotsRef.current[reportId] = snapshot;
  }, []);

  const loadExecutiveSummary = useCallback(async () => {
    if (reports.length === 0) {
      toast.error("No hay widgets visibles para resumir.");
      return null;
    }

    setIsExecutiveSummaryLoading(true);

    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) {
        throw new Error("No se encontró sesión activa.");
      }

      const widgetsPayload = reports.map((report) => (
        widgetSnapshotsRef.current[report.id]
        || buildExecutiveWidgetSnapshot({ report })
      ));

      const response = await fetch('http://localhost:8000/api/v1/presentations/executive-summary', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          presentation_id: activePresentationId || null,
          presentation_name: activePresentation?.name || 'Tablero Global',
          global_filters: normalizeExecutiveFilters(globalFilters || {}),
          widgets: widgetsPayload,
        }),
      });

      if (!response.ok) {
        throw new Error(`No se pudo generar el resumen ejecutivo (${response.status})`);
      }

      const payload = await response.json();
      setExecutiveSummary(payload);
      return payload as DashboardExecutiveSummary;
    } catch (error) {
      console.error("Error generando narrativa ejecutiva", error);
      toast.error("No se pudo generar el resumen ejecutivo del lienzo.");
      setExecutiveSummary(null);
      return null;
    } finally {
      setIsExecutiveSummaryLoading(false);
    }
  }, [activePresentation?.name, activePresentationId, getDashboardAccessToken, globalFilters, reports]);

  const handleOpenExecutiveSummary = useCallback(async () => {
    setIsExecutiveSummaryOpen(true);
    await loadExecutiveSummary();
  }, [loadExecutiveSummary]);

  const handleTogglePresentationSummary = useCallback(async () => {
    if (presentationSummaryVisible) {
      setPresentationSummaryVisible(false);
      return;
    }

    if (!executiveSummary) {
      const payload = await loadExecutiveSummary();
      if (!payload) return;
    }

    setPresentationSummaryVisible(true);
  }, [executiveSummary, loadExecutiveSummary, presentationSummaryVisible]);

  const ensureLibraryTargetPresentationId = useCallback(async (): Promise<string | null> => {
    if (activePresentationId) return activePresentationId;

    const existingGlobalPresentation = safePresentations.find((presentation) => {
      const fileId = typeof presentation?.file_id === 'string' ? presentation.file_id.trim() : '';
      return !fileId;
    });

    if (existingGlobalPresentation?.id) {
      setActivePresentationId(existingGlobalPresentation.id);
      return existingGlobalPresentation.id;
    }

    const accessToken = await getDashboardAccessToken();
    if (!accessToken) return null;

    const createResponse = await fetch('http://localhost:8000/api/v1/presentations', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${accessToken}`
      },
      body: JSON.stringify({
        name: 'Tablero Global',
        file_id: null
      })
    });

    if (!createResponse.ok) {
      throw new Error(`No se pudo crear presentación global (${createResponse.status})`);
    }

    const created = await createResponse.json().catch(() => null);
    const createdId = typeof created?.id === 'string' ? created.id : null;
    if (!createdId) return null;

    await fetchPresentations(createdId);
    return createdId;
  }, [activePresentationId, fetchPresentations, getDashboardAccessToken, safePresentations, setActivePresentationId]);

  const sanitizeClonedReportContent = (content: unknown): Record<string, unknown> => {
    const cloned = typeof structuredClone === 'function'
      ? structuredClone(content ?? {})
      : JSON.parse(JSON.stringify(content ?? {}));

    if (!cloned || typeof cloned !== 'object') {
      return {};
    }

    const payload = cloned as Record<string, unknown>;
    delete payload.layout;

    const innerContent = payload.content;
    if (innerContent && typeof innerContent === 'object') {
      delete (innerContent as Record<string, unknown>).layout;
    }

    return payload;
  };

  const handleInjectLibraryReport = useCallback(async (sourceReport: SavedReport) => {
    setInjectingLibraryReportId(sourceReport.id);
    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return;

      const targetPresentationId = await ensureLibraryTargetPresentationId();
      if (!targetPresentationId) {
        toast.error("No se pudo resolver una presentación destino para agregar el gráfico.");
        return;
      }

      const contentPayload = sanitizeClonedReportContent(sourceReport.content);
      const saveResponse = await fetch('http://localhost:8000/api/v1/reports', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({
          title: sourceReport.title,
          content: contentPayload,
          file_id: sourceReport.file_id || null,
          presentation_id: targetPresentationId
        })
      });

      if (!saveResponse.ok) {
        throw new Error(`No se pudo agregar widget desde biblioteca (${saveResponse.status})`);
      }

      if (activePresentationId === targetPresentationId) {
        await refreshReports({ silent: true, presentationId: targetPresentationId });
      } else {
        setActivePresentationId(targetPresentationId);
      }

      toast.success(`Widget agregado a ${activePresentationId === targetPresentationId ? 'la presentación actual' : 'la presentación seleccionada'}.`);
    } catch (error) {
      console.error("Error agregando reporte desde biblioteca", error);
      toast.error("No se pudo agregar el gráfico al lienzo.");
    } finally {
      setInjectingLibraryReportId(null);
    }
  }, [activePresentationId, ensureLibraryTargetPresentationId, getDashboardAccessToken, refreshReports, setActivePresentationId]);

  const resolveDefaultPresentationFileId = useCallback((): string | null => {
    const activeFileId = typeof activePresentation?.file_id === "string" ? activePresentation.file_id.trim() : "";
    if (activeFileId) return activeFileId;
    const reportFileId = reports.find((report) => typeof report.file_id === "string" && report.file_id.trim())?.file_id;
    return reportFileId ? reportFileId.trim() : null;
  }, [activePresentation?.file_id, reports]);

  const handleOpenCreatePresentation = useCallback(() => {
    setCreatePresentationName("Presentación ejecutiva");
    setIsCreatePresentationOpen(true);
  }, []);

  const handleOpenRenamePresentation = useCallback(() => {
    if (!activePresentation) {
      toast.error("Selecciona una presentación para renombrar.");
      return;
    }
    setRenamePresentationName(activePresentation.name || "");
    setIsRenamePresentationOpen(true);
  }, [activePresentation]);

  const handleOpenDuplicatePresentation = useCallback(() => {
    if (!activePresentationId || !activePresentation) {
      toast.error("Selecciona una presentación para duplicar.");
      return;
    }
    setDuplicatePresentationName(`${activePresentation.name} (Copia)`);
    setIsDuplicatePresentationOpen(true);
  }, [activePresentation, activePresentationId]);

  const handleCreatePresentation = useCallback(async () => {
    const name = createPresentationName.trim();
    if (!name) {
      toast.error("Ingresa un nombre para la presentación.");
      return;
    }

    const fileId = resolveDefaultPresentationFileId();
    if (!fileId) {
      toast.error("No se pudo detectar el archivo base (file_id) para crear la presentación.");
      return;
    }

    setIsPresentationActionLoading(true);
    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return;

      const response = await fetch('http://localhost:8000/api/v1/presentations', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({ name, file_id: fileId })
      });

      if (!response.ok) {
        throw new Error(`No se pudo crear la presentación (${response.status})`);
      }

      const created = await response.json().catch(() => null);
      const createdId = created?.id || null;
      await fetchPresentations(createdId);
      setIsCreatePresentationOpen(false);
      setCreatePresentationName("");
      toast.success(`Presentación creada: ${name}`);
    } catch (error) {
      console.error("Error creando presentación", error);
      toast.error("No se pudo crear la presentación.");
    } finally {
      setIsPresentationActionLoading(false);
    }
  }, [createPresentationName, fetchPresentations, getDashboardAccessToken, resolveDefaultPresentationFileId]);

  const handleRenamePresentation = useCallback(async () => {
    if (!activePresentationId || !activePresentation) {
      toast.error("Selecciona una presentación para renombrar.");
      return;
    }

    const name = renamePresentationName.trim();
    if (!name) {
      toast.error("Ingresa un nombre válido.");
      return;
    }

    setIsPresentationActionLoading(true);
    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return;

      const response = await fetch(`http://localhost:8000/api/v1/presentations/${activePresentationId}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({ name })
      });

      if (!response.ok) {
        throw new Error(`No se pudo renombrar la presentación (${response.status})`);
      }

      await fetchPresentations(activePresentationId);
      setIsRenamePresentationOpen(false);
      toast.success(`Presentación renombrada: ${name}`);
    } catch (error) {
      console.error("Error renombrando presentación", error);
      toast.error("No se pudo renombrar la presentación.");
    } finally {
      setIsPresentationActionLoading(false);
    }
  }, [activePresentation, activePresentationId, fetchPresentations, getDashboardAccessToken, renamePresentationName]);

  const handleDuplicatePresentation = useCallback(async () => {
    if (!activePresentationId || !activePresentation) {
      toast.error("Selecciona una presentación para duplicar.");
      return;
    }

    const name = duplicatePresentationName.trim();
    if (!name) {
      toast.error("Ingresa un nombre para la copia.");
      return;
    }

    const fileId = resolveDefaultPresentationFileId();
    if (!fileId) {
      toast.error("No se pudo detectar el archivo base (file_id) para duplicar.");
      return;
    }

    setIsPresentationActionLoading(true);
    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return;

      const createResponse = await fetch('http://localhost:8000/api/v1/presentations', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({ name, file_id: fileId })
      });

      if (!createResponse.ok) {
        throw new Error(`No se pudo crear la copia de presentación (${createResponse.status})`);
      }

      const createdPresentation = await createResponse.json();
      const duplicatedPresentationId = createdPresentation?.id;
      if (!duplicatedPresentationId) {
        throw new Error("La copia de presentación no devolvió ID.");
      }

      let copiedCount = 0;
      for (const sourceReport of reports) {
        const contentPayload = typeof structuredClone === "function"
          ? structuredClone(sourceReport.content ?? {})
          : JSON.parse(JSON.stringify(sourceReport.content ?? {}));

        const saveResponse = await fetch('http://localhost:8000/api/v1/reports', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${accessToken}`
          },
          body: JSON.stringify({
            title: sourceReport.title,
            content: contentPayload,
            file_id: sourceReport.file_id || fileId,
            presentation_id: duplicatedPresentationId,
          })
        });

        if (!saveResponse.ok) {
          throw new Error(`No se pudo copiar widget "${sourceReport.title}" (${saveResponse.status})`);
        }
        copiedCount += 1;
      }

      await fetchPresentations(duplicatedPresentationId);
      setIsDuplicatePresentationOpen(false);
      toast.success(`Presentación duplicada (${copiedCount} widget${copiedCount === 1 ? '' : 's'})`);
    } catch (error) {
      console.error("Error duplicando presentación", error);
      toast.error("No se pudo duplicar la presentación.");
    } finally {
      setIsPresentationActionLoading(false);
    }
  }, [
    activePresentation,
    activePresentationId,
    duplicatePresentationName,
    fetchPresentations,
    getDashboardAccessToken,
    reports,
    resolveDefaultPresentationFileId,
  ]);

  // Función para continuar análisis en el chat
  const handleAnalyze = useCallback((report: SavedReport) => {
    const recoveryContext = {
      parent_id: report.id,
      initial_content: report.title,
      mode: 'recovery'
    };
    localStorage.setItem('chat_recovery_context', JSON.stringify(recoveryContext));
    router.push(`/?fileId=${report.file_id}`);
  }, [router]);

  // Función para eliminar reporte
  const handleDeleteReport = useCallback(async (reportId: string) => {
    if (!confirm("¿Estás seguro de que deseas eliminar este reporte permanentemente?")) return;

    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return;

      const response = await fetch(`http://localhost:8000/api/v1/reports/${reportId}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${accessToken}` }
      });

      if (!response.ok) throw new Error("Error al eliminar");

      // Actualizar estado local
      setReports((prev: SavedReport[]) => prev.filter((r: SavedReport) => r.id !== reportId));
      setLayoutOverrides((prev) => {
        if (!prev[reportId]) return prev;
        const next = { ...prev };
        delete next[reportId];
        return next;
      });
      delete pendingLayoutRef.current[reportId];
      toast.success("Reporte eliminado");
    } catch (error) {
      toast.error("No se pudo eliminar el reporte");
      console.error(error);
    }
  }, [getDashboardAccessToken]);

  const handleCrossFilter = useCallback(async (filters: Record<string, string>, sourceFileId?: string | null) => {
    const rawSelectedValue = (
      filters.category && filters.category !== 'undefined'
        ? filters.category
        : filters.series
    )?.trim();

    const selectedValue = rawSelectedValue ? normalizeGlobalFilterValue(rawSelectedValue) : '';
    const currentFilter = typeof globalFilters.global_cross_filter === 'string'
      ? normalizeGlobalFilterValue(globalFilters.global_cross_filter)
      : '';
    const currentScopedFileId = typeof globalFilters.__scope_file_id === 'string'
      ? globalFilters.__scope_file_id.trim()
      : '';
    const nextScopedFileId = typeof sourceFileId === 'string' ? sourceFileId.trim() : '';

    if (!selectedValue) {
      return;
    }

    const shouldClearFilter = areEquivalentFilterValues(currentFilter, selectedValue)
      && currentScopedFileId === nextScopedFileId;

    const nextFilters: Record<string, string> = shouldClearFilter
      ? {}
      : {
          global_cross_filter: selectedValue,
          ...(nextScopedFileId ? { __scope_file_id: nextScopedFileId } : {}),
        };

    setGlobalFilters(nextFilters);
    toast.success(
      nextFilters.global_cross_filter
        ? `Filtro global aplicado: ${selectedValue}`
        : "Filtro global limpiado"
    );
  }, [areEquivalentFilterValues, globalFilters.__scope_file_id, globalFilters.global_cross_filter, setGlobalFilters]);

  const handleChartDrillDown = useCallback((params: any, tableName?: string, sourceFileId?: string) => {
    const rawCategory = extractRawChartCategory(params);
    if (rawCategory) {
      void handleCrossFilter({ category: rawCategory }, sourceFileId);
    }
  }, [handleCrossFilter]);

  const handleDeletePresentation = useCallback(async () => {
    if (!activePresentationId || !activePresentation) {
      toast.error("Selecciona una presentación para eliminarla.");
      return;
    }

    setIsPresentationActionLoading(true);
    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return;

      const response = await fetch(`http://localhost:8000/api/v1/presentations/${activePresentationId}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${accessToken}` }
      });

      if (!response.ok && response.status !== 204) {
        throw new Error(`No se pudo eliminar la presentación (${response.status})`);
      }

      const previousPresentations = safePresentations.filter((presentation) => presentation.id !== activePresentationId);
      const nextActivePresentationId = previousPresentations[0]?.id || null;
      await fetchPresentations(nextActivePresentationId);
      setIsDeletePresentationOpen(false);
      toast.success(`Presentación eliminada: ${activePresentation.name}`);
    } catch (error) {
      console.error("Error eliminando presentación", error);
      toast.error("No se pudo eliminar la presentación.");
    } finally {
      setIsPresentationActionLoading(false);
    }
  }, [activePresentation, activePresentationId, fetchPresentations, getDashboardAccessToken, safePresentations]);

  const enterPresentationMode = useCallback(async () => {
    setPresentationMode(true);
  }, []);

  const exitPresentationMode = useCallback(async () => {
    setFocusedReportId(null);
    setPresentationMode(false);
  }, []);

  const handleRequestWidgetFocus = useCallback((report: SavedReport) => {
    setFocusedReportId(report.id);
  }, []);

  const clearWidgetFocus = useCallback(() => {
    setFocusedReportId(null);
  }, []);

  const handleExportPresentation = useCallback(async (format: "png" | "jpg" | "pdf") => {
    const exportNode = presentationRootRef.current;
    if (!exportNode) {
      toast.error("No se encontró un lienzo limpio para exportar.");
      return;
    }

    setIsPresentationExporting(format);
    try {
      if (format === 'pdf') {
        await exportDashboardAsPdf(exportNode, {
          fileName: `${exportBaseFileName}-presentacion`,
        });
      } else {
        await exportDashboardAsImage(exportNode, {
          fileName: `${exportBaseFileName}-presentacion`,
          format,
        });
      }
      toast.success(`Exportación ${format.toUpperCase()} lista.`);
    } catch (error) {
      console.error("Error exportando tablero ejecutivo", error);
      toast.error(`No se pudo exportar el tablero en ${format.toUpperCase()}.`);
    } finally {
      setIsPresentationExporting(null);
    }
  }, [exportBaseFileName]);

  const persistPendingLayout = useCallback(async () => {
    const requestVersion = layoutPersistRequestVersionRef.current + 1;
    layoutPersistRequestVersionRef.current = requestVersion;
    const requestPresentationId = activePresentationIdRef.current;

    const layoutMap = pendingLayoutRef.current;
    const reportsById = new Map(reports.map((report) => [report.id, report] as const));
    const persistedItems = Object.entries(layoutMap)
      .filter(([reportId]) => /^[0-9a-fA-F-]{36}$/.test(reportId))
      .filter(([reportId, layout]) => {
        const currentReport = reportsById.get(reportId);
        const currentLayout = currentReport?.content?.layout;
        if (!currentReport) return false;
        return (
          !currentLayout ||
          currentLayout.x !== layout.x ||
          currentLayout.y !== layout.y ||
          currentLayout.w !== layout.w ||
          currentLayout.h !== layout.h
        );
      })
      .map(([report_id, layout]) => ({ report_id, ...layout }));

    if (persistedItems.length === 0) {
      return;
    }

    try {
      const accessToken = await getDashboardAccessToken();
      if (!accessToken) return;

      const requestBody = JSON.stringify({ items: persistedItems });
      let response = await fetch('http://localhost:8000/api/v1/reports/layout', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${accessToken}`
        },
        body: requestBody
      });

      if (!response.ok && response.status >= 500 && response.status < 600) {
        await new Promise((resolve) => setTimeout(resolve, 220));
        response = await fetch('http://localhost:8000/api/v1/reports/layout', {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${accessToken}`
          },
          body: requestBody
        });
      }

      const isStaleRequest = requestVersion !== layoutPersistRequestVersionRef.current
        || requestPresentationId !== activePresentationIdRef.current;
      if (isStaleRequest) {
        return;
      }

      if (!response.ok) {
        throw new Error(`Persistencia de layout falló (${response.status})`);
      }

      const persistedLayoutMap = persistedItems.reduce<Record<string, { x: number; y: number; w: number; h: number }>>((acc, item) => {
        acc[item.report_id] = { x: item.x, y: item.y, w: item.w, h: item.h };
        return acc;
      }, {});

      const currentCacheKey = getPresentationCacheKey(requestPresentationId);
      const cachedReports = reportsCacheRef.current[currentCacheKey];
      if (Array.isArray(cachedReports) && cachedReports.length > 0) {
        reportsCacheRef.current[currentCacheKey] = cachedReports.map((report) => {
          const nextLayout = persistedLayoutMap[report.id];
          if (!nextLayout) return report;
          return {
            ...report,
            content: {
              ...report.content,
              layout: nextLayout,
            }
          };
        });
      }

      pendingLayoutRef.current = Object.fromEntries(
        Object.entries(pendingLayoutRef.current).filter(([reportId]) => !persistedLayoutMap[reportId])
      );
    } catch (error) {
      const isStaleRequest = requestVersion !== layoutPersistRequestVersionRef.current
        || requestPresentationId !== activePresentationIdRef.current;
      if (isStaleRequest) {
        return;
      }
      console.error("Error persistiendo layout en backend", error);
      toast.error("No se pudo persistir el layout del tablero.");
    }
  }, [getDashboardAccessToken, reports]);

  const scheduleLayoutPersistence = useCallback(() => {
    if (layoutSaveTimeoutRef.current) {
      clearTimeout(layoutSaveTimeoutRef.current);
    }

    layoutSaveTimeoutRef.current = setTimeout(() => {
      void persistPendingLayout();
    }, 700);
  }, [persistPendingLayout]);

  const onLayoutCommit = useCallback((newLayout: any[]) => {
    const layoutMap = buildLayoutMap(newLayout);
    pendingLayoutRef.current = layoutMap;
    setLayoutOverrides((prev) => (areLayoutMapsEqual(prev, layoutMap) ? prev : layoutMap));
    scheduleLayoutPersistence();
  }, [scheduleLayoutPersistence]);

  // Pre-calcular config inicial del grid con tamaños inteligentes por tipo de contenido
  const initialLayout = useMemo(() => reports.map((r, i) => {
    const layout = layoutOverrides[r.id] || r.content.layout || {};
    const contentType = r.content.type || r.type || 'metrics';

    // Defaults dimensionales por tipo de widget
    const sizeDefaults: Record<string, { w: number; h: number; minW: number; minH: number }> = {
      chart:   { w: 6,  h: 4, minW: 4, minH: 3 },
      table:   { w: 12, h: 3, minW: 6, minH: 2 },
      metrics: { w: 4,  h: 3, minW: 3, minH: 2 },
    };
    const defaults = sizeDefaults[contentType] || sizeDefaults.metrics;

    return {
      i: r.id,
      x: layout.x ?? (i * defaults.w) % 12,
      y: layout.y ?? Math.floor(i / Math.floor(12 / defaults.w)) * defaults.h,
      w: layout.w ?? defaults.w,
      h: layout.h ?? defaults.h,
      minW: defaults.minW,
      minH: defaults.minH
    };
  }), [layoutOverrides, reports]);

  const gridLayoutKey = useMemo(
    () => `${activePresentationId || 'global'}:${reports.map((report) => report.id).join('|')}`,
    [activePresentationId, reports],
  );

  const focusedReport = useMemo(
    () => reports.find((report) => report.id === focusedReportId) || null,
    [focusedReportId, reports],
  );

  const renderExecutiveSummaryPanel = () => {
    if (!executiveSummary || !presentationSummaryVisible) return null;

    return (
      <section className="mb-6 rounded-2xl border border-border/60 bg-card/95 p-6 shadow-sm">
        <div className="flex flex-col gap-3">
          <div className="text-xl font-semibold text-foreground">{executiveSummary.headline}</div>
          <p className="text-sm leading-6 text-muted-foreground">{executiveSummary.overview}</p>
          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span className="rounded-full border px-2.5 py-1">Widgets: {executiveSummary.widget_count}</span>
            {executiveSummary.mixed_sources && (
              <span className="rounded-full border px-2.5 py-1">Múltiples archivos</span>
            )}
            {(executiveSummary.filter_scope || []).map((entry) => (
              <span key={entry} className="rounded-full border px-2.5 py-1">
                {entry}
              </span>
            ))}
          </div>
        </div>

        <div className="mt-6 grid gap-4 xl:grid-cols-3">
          <div className="space-y-2 rounded-xl border border-border/60 bg-background/80 p-4">
            <h3 className="text-sm font-semibold text-foreground">Hallazgos</h3>
            {(executiveSummary.key_findings || []).length > 0 ? (
              <ul className="space-y-2 text-sm text-muted-foreground">
                {executiveSummary.key_findings.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <div className="text-sm text-muted-foreground">Sin hallazgos visibles para este alcance.</div>
            )}
          </div>
          <div className="space-y-2 rounded-xl border border-border/60 bg-background/80 p-4">
            <h3 className="text-sm font-semibold text-foreground">Riesgos</h3>
            {(executiveSummary.risks || []).length > 0 ? (
              <ul className="space-y-2 text-sm text-muted-foreground">
                {executiveSummary.risks.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <div className="text-sm text-muted-foreground">No se detectaron riesgos explícitos.</div>
            )}
          </div>
          <div className="space-y-2 rounded-xl border border-border/60 bg-background/80 p-4">
            <h3 className="text-sm font-semibold text-foreground">Acciones</h3>
            {(executiveSummary.actions || []).length > 0 ? (
              <ul className="space-y-2 text-sm text-muted-foreground">
                {executiveSummary.actions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <div className="text-sm text-muted-foreground">Sin acciones sugeridas para este alcance.</div>
            )}
          </div>
        </div>

        {(executiveSummary.caveats || []).length > 0 && (
          <div className="mt-4 rounded-xl border border-border/60 bg-background/80 p-4">
            <h3 className="text-sm font-semibold text-foreground">Limitaciones</h3>
            <ul className="mt-2 space-y-2 text-sm text-muted-foreground">
              {executiveSummary.caveats.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </section>
    );
  };

  return (
    <div
      ref={presentationShellRef}
      className={[
        "flex h-screen bg-muted/30 transition-colors duration-150",
        presentationMode ? "dashboard-presentation-shell bg-background" : "",
      ].join(" ")}
    >
      <div className={presentationMode ? "w-0 overflow-hidden pointer-events-none opacity-0" : undefined}>
        <Sidebar />
      </div>
      <main className={[
        "flex-1 flex flex-col overflow-hidden transition-[padding] duration-150",
        presentationMode ? "p-0" : "p-6",
      ].join(" ")}>
        {!presentationMode ? (
          <header className="mb-6 border-b pb-4 flex justify-between items-center sticky top-0 z-10 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
            <div>
              <h1 className="font-normal tracking-tight text-4xl text-foreground">
                Tablero de Control
              </h1>
              <p className="mt-2 text-sm font-light text-muted-foreground">
                Visión integral de tus indicadores clave.
              </p>
            </div>
            <div className="flex gap-4 items-center">
              <div className="relative inline-flex items-center">
                <Presentation className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <select 
                  className="appearance-none bg-background border border-input text-sm rounded-md pl-9 pr-8 py-2 focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent text-foreground h-9"
                  value={activePresentationId || ''}
                  onChange={(e) => setActivePresentationId(e.target.value || null)}
                  disabled={isPresentationActionLoading}
                >
                  {!activePresentationId && <option value="">Selecciona Presentación</option>}
                  <option value="">Todas (Lienzo Global Legacy)</option>
                  {safePresentations.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
                <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-muted-foreground">
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
                  </svg>
                </div>
              </div>
              <Button
                onClick={handleOpenCreatePresentation}
                variant="outline"
                size="icon"
                className="h-9 w-9"
                disabled={isPresentationActionLoading}
                title="Crear presentación"
              >
                <Plus className="h-4 w-4" />
              </Button>
              <Button
                onClick={handleOpenRenamePresentation}
                variant="outline"
                size="icon"
                className="h-9 w-9"
                disabled={!activePresentation || isPresentationActionLoading}
                title="Renombrar presentación"
              >
                <Pencil className="h-4 w-4" />
              </Button>
              <Button
                onClick={handleOpenDuplicatePresentation}
                variant="outline"
                size="icon"
                className="h-9 w-9"
                disabled={!activePresentation || isPresentationActionLoading}
                title="Duplicar presentación"
              >
                <Copy className="h-4 w-4" />
              </Button>
              <Button onClick={() => setIsDeletePresentationOpen(true)} variant="outline" size="icon" className="h-9 w-9" disabled={!activePresentation || isPresentationActionLoading} title="Eliminar presentación">
                <Trash2 className="h-4 w-4" />
              </Button>
              <Button
                onClick={handleOpenLibrary}
                variant="outline"
                size="sm"
                className="h-9"
                disabled={isPresentationActionLoading}
                title="Abrir biblioteca global"
              >
                <Library className="h-4 w-4 mr-2" />
                Biblioteca
              </Button>
              <Button
                onClick={() => void handleOpenExecutiveSummary()}
                variant="outline"
                size="sm"
                className="h-9"
                disabled={reports.length === 0 || isExecutiveSummaryLoading}
                title="Generar resumen ejecutivo"
              >
                {isExecutiveSummaryLoading ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <FileText className="h-4 w-4 mr-2" />
                )}
                Resumen Ejecutivo
              </Button>
              <Button
                onClick={() => void enterPresentationMode()}
                variant="outline"
                size="sm"
                className="h-9"
                disabled={reports.length === 0}
                title="Activar modo presentación"
              >
                Presentar
              </Button>

              <Button onClick={() => void refreshReports()} variant="outline" size="icon" title="Actualizar datos">
                <RefreshCw className="h-4 w-4" />
              </Button>
              {Object.keys(globalFilters || {}).length > 0 && (
                <Button
                  onClick={() => setGlobalFilters({})}
                  variant="outline"
                  size="sm"
                  className="h-9"
                  data-testid="dashboard-clear-filters"
                >
                  Limpiar Filtros
                </Button>
              )}
            </div>
          </header>
        ) : (
          <div className="dashboard-presentation-toolbar">
            <div className="text-sm font-medium text-foreground">
              {activePresentation?.name || 'Tablero Ejecutivo'}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                onClick={() => void handleTogglePresentationSummary()}
                variant="outline"
                size="sm"
                className="h-9"
                disabled={reports.length === 0 || isExecutiveSummaryLoading}
              >
                {isExecutiveSummaryLoading ? 'Generando resumen...' : presentationSummaryVisible ? 'Ocultar resumen' : 'Mostrar resumen'}
              </Button>
              {focusedReport ? (
                <Button onClick={clearWidgetFocus} variant="outline" size="sm" className="h-9">
                  Salir foco
                </Button>
              ) : null}
              <Button
                onClick={() => void handleExportPresentation('png')}
                variant="outline"
                size="sm"
                className="h-9"
                disabled={isPresentationExporting !== null}
              >
                {isPresentationExporting === 'png' ? 'Exportando...' : 'PNG'}
              </Button>
              <Button
                onClick={() => void handleExportPresentation('jpg')}
                variant="outline"
                size="sm"
                className="h-9"
                disabled={isPresentationExporting !== null}
              >
                {isPresentationExporting === 'jpg' ? 'Exportando...' : 'JPG'}
              </Button>
              <Button
                onClick={() => void handleExportPresentation('pdf')}
                variant="outline"
                size="sm"
                className="h-9"
                disabled={isPresentationExporting !== null}
              >
                {isPresentationExporting === 'pdf' ? 'Preparando PDF...' : 'PDF'}
              </Button>
              <Button onClick={() => void exitPresentationMode()} size="sm" className="h-9">
                Salir
              </Button>
            </div>
          </div>
        )}

        <div className={[
          "flex-1 overflow-y-auto print:overflow-visible",
          presentationMode ? "px-6 pb-8 pt-1" : "",
        ].join(" ")}>
          <div
            ref={presentationRootRef}
            className={presentationMode ? "dashboard-presentation-root mx-auto w-full max-w-[1680px]" : undefined}
          >
            {presentationMode && renderExecutiveSummaryPanel()}

            {loading && reports.length === 0 ? (
              <div className="flex items-center justify-center h-40">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
              </div>
            ) : reports.length === 0 ? (
              <div className="text-center py-20 bg-muted/20 rounded-xl border border-dashed">
                <h3 className="text-xl font-medium text-foreground">No tienes reportes guardados</h3>
                <p className="text-muted-foreground mt-2">
                  Guarda métricas o gráficos desde el chat para armar tu tablero.
                </p>
              </div>
            ) : (
              <ResponsiveGridLayout
                key={gridLayoutKey}
                className="layout"
                layouts={{ lg: initialLayout }}
                breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480, xxs: 0 }}
                cols={{ lg: 12, md: 10, sm: 6, xs: 4, xxs: 2 }}
                rowHeight={presentationMode ? 82 : 100}
                onDragStop={onLayoutCommit}
                onResizeStop={onLayoutCommit}
                draggableHandle=".widget-drag-handle"
                isDraggable={!presentationMode}
                isResizable={!presentationMode}
                resizeHandles={['s', 'w', 'e', 'n', 'sw', 'nw', 'se', 'ne']}
                margin={presentationMode ? [20, 20] : [24, 24]}
              >
                {reports.map((report: SavedReport, index: number) => (
                  <div key={report.id}>
                    <GridWidget 
                      report={report as any} 
                      onDelete={handleDeleteReport}
                      onAnalyze={handleAnalyze}
                      onChartClick={handleChartDrillDown}
                      onNarrativeSnapshotChange={handleNarrativeSnapshotChange}
                      presentationMode={presentationMode}
                      onRequestFocus={presentationMode ? handleRequestWidgetFocus : undefined}
                      recomputePriority={index}
                    />
                  </div>
                ))}
              </ResponsiveGridLayout>
            )}
          </div>
        </div>
      </main >

      <Dialog
        open={Boolean(presentationMode && focusedReport)}
        onOpenChange={(open) => {
          if (!open) {
            clearWidgetFocus();
          }
        }}
      >
        <DialogContent className="dashboard-presentation-dialog">
          <DialogHeader className="sr-only">
            <DialogTitle>{focusedReport?.title || 'Visual enfocado'}</DialogTitle>
            <DialogDescription>
              Vista enfocada del widget seleccionado en modo presentación.
            </DialogDescription>
          </DialogHeader>
          {focusedReport ? (
            <div className="dashboard-presentation-focus">
              <GridWidget
                report={focusedReport as any}
                onDelete={handleDeleteReport}
                onAnalyze={handleAnalyze}
                onChartClick={handleChartDrillDown}
                onNarrativeSnapshotChange={handleNarrativeSnapshotChange}
                presentationMode={true}
                recomputePriority={0}
              />
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={isDeletePresentationOpen} onOpenChange={setIsDeletePresentationOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Eliminar Presentación</DialogTitle>
            <DialogDescription className="sr-only">
              Confirma la eliminación permanente de la presentación activa.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2 text-sm text-muted-foreground">
            {activePresentation ? `Se eliminará "${activePresentation.name}" junto con sus widgets guardados.` : "No hay presentación activa."}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsDeletePresentationOpen(false)}
              disabled={isPresentationActionLoading}
            >
              Cancelar
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeletePresentation}
              disabled={isPresentationActionLoading || !activePresentation}
            >
              Eliminar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isLibraryOpen} onOpenChange={setIsLibraryOpen}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>Biblioteca de Gráficos</DialogTitle>
            <DialogDescription className="sr-only">
              Inserta gráficos guardados de cualquier archivo en la presentación actual.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 min-h-0 flex-1">
            <Input
              value={librarySearch}
              onChange={(event) => setLibrarySearch(event.target.value)}
              placeholder="Buscar por título o file_id"
            />
            <select
              value={selectedLibraryPresentationId}
              onChange={(event) => handleLibraryPresentationChange(event.target.value)}
              className="w-full px-3 py-2 rounded-md border text-sm outline-none focus:ring-2 ring-primary/30 bg-background"
            >
              <option value="">Seleccionar lienzo</option>
              <option value="__all__">Todos los lienzos</option>
              {safePresentations.map((presentation) => (
                <option key={presentation.id} value={presentation.id}>
                  {presentation.name}
                </option>
              ))}
            </select>
            <div className="h-[min(58vh,520px)] overflow-y-auto overscroll-contain rounded-md border">
              {!selectedLibraryPresentationId ? (
                <div className="p-6 text-sm text-muted-foreground">Selecciona un lienzo para ver sus gráficos.</div>
              ) : isLibraryLoading ? (
                <div className="p-6 text-sm text-muted-foreground">Cargando gráficos del lienzo...</div>
              ) : filteredLibraryReports.length === 0 ? (
                <div className="p-6 text-sm text-muted-foreground">No hay gráficos disponibles para insertar.</div>
              ) : (
                <div className="divide-y">
                  {filteredLibraryReports.map((report) => {
                    const presentationId = typeof (report as any)?.presentation_id === 'string'
                      ? (report as any).presentation_id
                      : '';
                    const presentationLabel = presentationNameById.get(presentationId) || (presentationId ? presentationId : 'Sin lienzo');
                    return (
                      <div key={report.id} className="flex items-center justify-between gap-4 p-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium text-foreground">{report.title}</div>
                          <div className="truncate text-xs text-muted-foreground">
                            Lienzo: {presentationLabel} · file_id: {report.file_id || 'sin archivo'} · {report.content?.type || 'chart'}
                          </div>
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={injectingLibraryReportId === report.id}
                          onClick={() => void handleInjectLibraryReport(report)}
                        >
                          {injectingLibraryReportId === report.id ? 'Agregando...' : 'Agregar'}
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsLibraryOpen(false)}>
              Cerrar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isExecutiveSummaryOpen} onOpenChange={setIsExecutiveSummaryOpen}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>Resumen Ejecutivo</DialogTitle>
            <DialogDescription>
              Síntesis ejecutiva del lienzo actual basada en los widgets visibles y filtros activos.
            </DialogDescription>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto pr-1">
            {isExecutiveSummaryLoading ? (
              <div className="flex min-h-[260px] items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Generando síntesis ejecutiva...
              </div>
            ) : !executiveSummary ? (
              <div className="flex min-h-[260px] items-center justify-center text-sm text-muted-foreground">
                No hay resumen disponible para este lienzo.
              </div>
            ) : (
              <div className="space-y-5">
                <section className="rounded-xl border bg-muted/20 p-4">
                  <div className="text-base font-semibold text-foreground">{executiveSummary.headline}</div>
                  <p className="mt-2 text-sm leading-6 text-muted-foreground">{executiveSummary.overview}</p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span className="rounded-full border px-2 py-1">
                      Widgets: {executiveSummary.widget_count}
                    </span>
                    {executiveSummary.mixed_sources && (
                      <span className="rounded-full border px-2 py-1">
                        Múltiples archivos
                      </span>
                    )}
                    {(executiveSummary.filter_scope || []).map((filterValue) => (
                      <span key={filterValue} className="rounded-full border px-2 py-1">
                        {filterValue}
                      </span>
                    ))}
                  </div>
                </section>

                <section className="space-y-2">
                  <h3 className="text-sm font-semibold text-foreground">Hallazgos clave</h3>
                  {(executiveSummary.key_findings || []).length > 0 ? (
                    <ul className="space-y-2 text-sm text-muted-foreground">
                      {executiveSummary.key_findings.map((item) => (
                        <li key={item} className="rounded-lg border bg-background px-3 py-2">{item}</li>
                      ))}
                    </ul>
                  ) : (
                    <div className="text-sm text-muted-foreground">Sin hallazgos adicionales visibles.</div>
                  )}
                </section>

                <section className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold text-foreground">Riesgos</h3>
                    {(executiveSummary.risks || []).length > 0 ? (
                      <ul className="space-y-2 text-sm text-muted-foreground">
                        {executiveSummary.risks.map((item) => (
                          <li key={item} className="rounded-lg border bg-background px-3 py-2">{item}</li>
                        ))}
                      </ul>
                    ) : (
                      <div className="text-sm text-muted-foreground">No se detectaron riesgos explícitos con la evidencia visible.</div>
                    )}
                  </div>
                  <div className="space-y-2">
                    <h3 className="text-sm font-semibold text-foreground">Acciones</h3>
                    {(executiveSummary.actions || []).length > 0 ? (
                      <ul className="space-y-2 text-sm text-muted-foreground">
                        {executiveSummary.actions.map((item) => (
                          <li key={item} className="rounded-lg border bg-background px-3 py-2">{item}</li>
                        ))}
                      </ul>
                    ) : (
                      <div className="text-sm text-muted-foreground">No hay acciones sugeridas para este alcance.</div>
                    )}
                  </div>
                </section>

                {(executiveSummary.caveats || []).length > 0 && (
                  <section className="space-y-2">
                    <h3 className="text-sm font-semibold text-foreground">Limitaciones</h3>
                    <ul className="space-y-2 text-sm text-muted-foreground">
                      {executiveSummary.caveats.map((item) => (
                        <li key={item} className="rounded-lg border bg-background px-3 py-2">{item}</li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsExecutiveSummaryOpen(false)}>
              Cerrar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isCreatePresentationOpen} onOpenChange={setIsCreatePresentationOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Crear Presentación</DialogTitle>
            <DialogDescription className="sr-only">
              Crea una nueva presentación ejecutiva para el dataset activo.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2 space-y-2">
            <Input
              value={createPresentationName}
              onChange={(event) => setCreatePresentationName(event.target.value)}
              placeholder="Nombre de la presentación"
              disabled={isPresentationActionLoading}
              autoFocus
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  void handleCreatePresentation();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsCreatePresentationOpen(false)}
              disabled={isPresentationActionLoading}
            >
              Cancelar
            </Button>
            <Button
              onClick={() => void handleCreatePresentation()}
              disabled={isPresentationActionLoading}
            >
              Crear
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isRenamePresentationOpen} onOpenChange={setIsRenamePresentationOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Renombrar Presentación</DialogTitle>
            <DialogDescription className="sr-only">
              Actualiza el nombre de la presentación seleccionada.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2 space-y-2">
            <Input
              value={renamePresentationName}
              onChange={(event) => setRenamePresentationName(event.target.value)}
              placeholder="Nuevo nombre"
              disabled={isPresentationActionLoading}
              autoFocus
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  void handleRenamePresentation();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsRenamePresentationOpen(false)}
              disabled={isPresentationActionLoading}
            >
              Cancelar
            </Button>
            <Button
              onClick={() => void handleRenamePresentation()}
              disabled={isPresentationActionLoading || !activePresentation}
            >
              Guardar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isDuplicatePresentationOpen} onOpenChange={setIsDuplicatePresentationOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Duplicar Presentación</DialogTitle>
            <DialogDescription className="sr-only">
              Crea una copia de la presentación y de sus widgets guardados.
            </DialogDescription>
          </DialogHeader>
          <div className="py-2 space-y-2">
            <Input
              value={duplicatePresentationName}
              onChange={(event) => setDuplicatePresentationName(event.target.value)}
              placeholder="Nombre de la copia"
              disabled={isPresentationActionLoading}
              autoFocus
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  void handleDuplicatePresentation();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsDuplicatePresentationOpen(false)}
              disabled={isPresentationActionLoading}
            >
              Cancelar
            </Button>
            <Button
              onClick={() => void handleDuplicatePresentation()}
              disabled={isPresentationActionLoading || !activePresentation}
            >
              Duplicar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground">Cargando tablero...</div>}>
      <DashboardPageClient />
    </Suspense>
  );
}
