import { expect, Page, test } from '@playwright/test';
import { tableFromJSON, tableToIPC } from 'apache-arrow';

const CRITICAL_ERROR_RE = /(getRawIndex|getDataParams|Cannot read properties of undefined.*getRawIndex|TreemapSeries)/i;
const SUPABASE_STORAGE_KEY = 'sb-dxlkejsrvuknuajkltwm-auth-token';
const FILE_ID = '06d713f2-f413-4ce0-89f7-be6add621255';
const PRESENTATION_ID = '11111111-1111-4111-8111-111111111111';
const HYBRID_REPORT_ID = '22222222-2222-4222-8222-222222222222';
const BAR_REPORT_ID = '33333333-3333-4333-8333-333333333333';

function trackCriticalErrors(page: Page): string[] {
  const criticalErrors: string[] = [];

  page.on('pageerror', (error) => {
    const message = error?.message || String(error);
    if (CRITICAL_ERROR_RE.test(message)) {
      criticalErrors.push(message);
    }
  });

  page.on('console', (msg) => {
    if (msg.type() !== 'error') return;
    const text = msg.text();
    if (CRITICAL_ERROR_RE.test(text)) {
      criticalErrors.push(text);
    }
  });

  return criticalErrors;
}

function encodeArrowBase64(rows: Array<Record<string, unknown>>): string {
  const table = tableFromJSON(rows);
  const bytes = tableToIPC(table, 'stream');
  return Buffer.from(bytes).toString('base64');
}

function buildDashboardReports() {
  const granularRows = [
    { mes: 'Mar-2021', almacen: 'Almacen Norte', stock_disponible: 100 },
    { mes: 'Apr-2021', almacen: 'Almacen Norte', stock_disponible: 120 },
    { mes: 'May-2021', almacen: 'Almacen Norte', stock_disponible: 140 },
    { mes: 'Mar-2021', almacen: 'Almacen Sur', stock_disponible: 80 },
    { mes: 'Apr-2021', almacen: 'Almacen Sur', stock_disponible: 90 },
    { mes: 'May-2021', almacen: 'Almacen Sur', stock_disponible: 60 },
  ];

  const granularArrow = encodeArrowBase64(granularRows);

  const hybridReport = {
    id: HYBRID_REPORT_ID,
    title: 'Evolución del Stock Disponible',
    file_id: FILE_ID,
    created_at: '2026-04-13T08:00:00.000Z',
    content: {
      type: 'table',
      layout: { x: 0, y: 0, w: 6, h: 4 },
      content: {
        title: 'Evolución del Stock Disponible',
        default_view_mode: 'chart',
        columns: [
          { key: 'mes', label: 'Mes', type: 'text' },
          { key: 'stock_disponible', label: 'Stock Disponible', type: 'number', bar: true },
        ],
        data: [
          { mes: 'Mar-2021', stock_disponible: 180 },
          { mes: 'Apr-2021', stock_disponible: 210 },
          { mes: 'May-2021', stock_disponible: 200 },
        ],
        original_chart_option: {
          tooltip: { trigger: 'axis' },
          xAxis: { type: 'category', data: ['Mar-2021', 'Apr-2021', 'May-2021'] },
          yAxis: { type: 'value' },
          series: [
            {
              type: 'line',
              name: 'Stock Disponible',
              smooth: true,
              areaStyle: {},
              data: [180, 210, 200],
            },
          ],
          granular_arrow: granularArrow,
        },
      },
    },
  };

  const barReport = {
    id: BAR_REPORT_ID,
    title: 'Top 2 almacenes principales',
    file_id: FILE_ID,
    created_at: '2026-04-13T08:00:00.000Z',
    content: {
      type: 'chart',
      layout: { x: 6, y: 0, w: 6, h: 4 },
      content: {
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: 64, right: 24, top: 16, bottom: 48 },
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: ['Almacen Norte', 'Almacen Sur'] },
        series: [
          {
            type: 'bar',
            name: 'Stock Disponible',
            data: [140, 60],
            label: { show: true, position: 'right' },
          },
        ],
        granular_arrow: granularArrow,
        query_contract: {
          intent_type: 'distribution',
          aggregation: 'sum',
          dimension: 'almacen',
          metric: 'stock_disponible',
          limit: 2,
        },
      },
    },
  };

  return [hybridReport, barReport];
}

async function installMockSession(page: Page): Promise<void> {
  const session = {
    access_token: 'playwright-token',
    refresh_token: 'playwright-refresh',
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user: {
      id: 'playwright-user',
      aud: 'authenticated',
      role: 'authenticated',
      email: 'playwright@promdata.local',
    },
  };

  await page.addInitScript(
    ([storageKey, payload]) => {
      window.localStorage.setItem(storageKey, JSON.stringify(payload));
    },
    [SUPABASE_STORAGE_KEY, session] as const
  );
}

async function mockDashboardApis(page: Page): Promise<void> {
  const reports = buildDashboardReports();

  await page.route('http://localhost:8000/api/v1/presentations**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: PRESENTATION_ID,
          name: 'QA Presentacion Persistida',
          file_id: FILE_ID,
          created_at: '2026-04-13T08:00:00.000Z',
        },
      ]),
    });
  });

  await page.route('http://localhost:8000/api/v1/reports/layout', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true }),
    });
  });

  await page.route('http://localhost:8000/api/v1/reports**', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(reports),
    });
  });
}

test.describe('Dashboard persisted cross-filter', () => {
  test('Persisted widgets: click -> filter -> infer contract -> clear -> reload', async ({ page }) => {
    const criticalErrors = trackCriticalErrors(page);
    const inferredContractLogs: string[] = [];

    page.on('console', (msg) => {
      const text = msg.text();
      if (text.includes('🧠 [DASHBOARD] Query contract inferido para widget reactivo')) {
        inferredContractLogs.push(text);
      }
    });

    await installMockSession(page);
    await mockDashboardApis(page);

    await page.goto('/dashboard?__qa_dashboard=1&__qa_dashboard_token=playwright-token');

    await expect(page.getByTestId(`dashboard-widget-${HYBRID_REPORT_ID}`)).toBeVisible();
    await expect(page.getByTestId(`dashboard-widget-${BAR_REPORT_ID}`)).toBeVisible();
    await expect(
      page.getByTestId(`dashboard-widget-${HYBRID_REPORT_ID}`).getByRole('button', { name: /Ver Tabla/i })
    ).toBeVisible();

    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent('promdata:qa-dashboard-filter', {
        detail: { category: 'Almacen Norte' },
      }));
    });

    await expect(page.getByTestId('dashboard-clear-filters')).toBeVisible();
    await expect
      .poll(() => inferredContractLogs.length, { timeout: 10000 })
      .toBeGreaterThan(0);

    await page.getByTestId('dashboard-clear-filters').click();
    await expect(page.getByTestId('dashboard-clear-filters')).toHaveCount(0);

    await page.reload();
    await expect(page.getByTestId(`dashboard-widget-${HYBRID_REPORT_ID}`)).toBeVisible();
    await expect(page.getByTestId(`dashboard-widget-${BAR_REPORT_ID}`)).toBeVisible();

    expect(criticalErrors).toEqual([]);
  });
});
