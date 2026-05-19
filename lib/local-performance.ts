"use client";

export type LocalPerfMetricName =
  | "duckdb_query"
  | "duckdb_cross_filter"
  | "dashboard_widget_recompute";

type LocalPerfMetricSummary = {
  count: number;
  avgMs: number;
  maxMs: number;
  lastMs: number;
  slowCount: number;
  lastDetail: Record<string, unknown> | null;
};

type LocalPerfScopeSummary = {
  count: number;
  avgMs: number;
  maxMs: number;
  lastMs: number;
};

export type LocalPerfSnapshot = {
  version: 1;
  updatedAt: string;
  metrics: Partial<Record<LocalPerfMetricName, LocalPerfMetricSummary>>;
  scopes: Partial<Record<LocalPerfMetricName, Record<string, LocalPerfScopeSummary>>>;
};

declare global {
  interface Window {
    __PROMDATA_LOCAL_PERF__?: LocalPerfSnapshot;
  }
}

const SLOW_THRESHOLD_MS: Record<LocalPerfMetricName, number> = {
  duckdb_query: 40,
  duckdb_cross_filter: 75,
  dashboard_widget_recompute: 120,
};

function createEmptySnapshot(): LocalPerfSnapshot {
  return {
    version: 1,
    updatedAt: new Date().toISOString(),
    metrics: {},
    scopes: {},
  };
}

function getSnapshot(): LocalPerfSnapshot | null {
  if (typeof window === "undefined") return null;
  if (!window.__PROMDATA_LOCAL_PERF__) {
    window.__PROMDATA_LOCAL_PERF__ = createEmptySnapshot();
  }
  return window.__PROMDATA_LOCAL_PERF__;
}

export function recordLocalPerf(
  metricName: LocalPerfMetricName,
  durationMs: number,
  detail: Record<string, unknown> = {},
  scopeKey?: string
): void {
  if (typeof window === "undefined") return;
  if (!Number.isFinite(durationMs)) return;

  const snapshot = getSnapshot();
  if (!snapshot) return;

  const previous = snapshot.metrics[metricName];
  const nextCount = (previous?.count || 0) + 1;
  const nextAvg = previous
    ? ((previous.avgMs * previous.count) + durationMs) / nextCount
    : durationMs;
  const threshold = SLOW_THRESHOLD_MS[metricName];
  const isSlow = durationMs >= threshold;

  snapshot.metrics[metricName] = {
    count: nextCount,
    avgMs: Number(nextAvg.toFixed(2)),
    maxMs: Math.max(previous?.maxMs || 0, durationMs),
    lastMs: Number(durationMs.toFixed(2)),
    slowCount: (previous?.slowCount || 0) + (isSlow ? 1 : 0),
    lastDetail: detail,
  };

  if (scopeKey && scopeKey.trim()) {
    const normalizedScopeKey = scopeKey.trim();
    const metricScopes = snapshot.scopes[metricName] || {};
    const previousScope = metricScopes[normalizedScopeKey];
    const nextScopeCount = (previousScope?.count || 0) + 1;
    const nextScopeAvg = previousScope
      ? ((previousScope.avgMs * previousScope.count) + durationMs) / nextScopeCount
      : durationMs;

    snapshot.scopes[metricName] = {
      ...metricScopes,
      [normalizedScopeKey]: {
        count: nextScopeCount,
        avgMs: Number(nextScopeAvg.toFixed(2)),
        maxMs: Math.max(previousScope?.maxMs || 0, durationMs),
        lastMs: Number(durationMs.toFixed(2)),
      },
    };
  }

  snapshot.updatedAt = new Date().toISOString();
  window.__PROMDATA_LOCAL_PERF__ = snapshot;

  if (process.env.NODE_ENV !== "production" && isSlow) {
    console.warn("⚠️ [PROMDATA PERF] slow_local_metric", {
      metric: metricName,
      durationMs: Number(durationMs.toFixed(2)),
      thresholdMs: threshold,
      detail,
    });
  }
}

export function startLocalPerf(
  metricName: LocalPerfMetricName,
  baseDetail: Record<string, unknown> = {},
  scopeKey?: string
): (detail?: Record<string, unknown>) => void {
  const startedAt = performance.now();

  return (detail: Record<string, unknown> = {}) => {
    const durationMs = performance.now() - startedAt;
    recordLocalPerf(metricName, durationMs, {
      ...baseDetail,
      ...detail,
    }, scopeKey);
  };
}

export function getLocalPerfSnapshot(): LocalPerfSnapshot | null {
  return getSnapshot();
}

export function getScopedLocalPerfAverage(
  metricName: LocalPerfMetricName,
  scopeKey: string
): number | null {
  if (!scopeKey.trim()) return null;
  const snapshot = getSnapshot();
  if (!snapshot) return null;

  const scopedMetric = snapshot.scopes[metricName]?.[scopeKey.trim()];
  if (!scopedMetric || !Number.isFinite(scopedMetric.avgMs)) {
    return null;
  }

  return scopedMetric.avgMs;
}
