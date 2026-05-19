import { expect, Page, test } from '@playwright/test';
import { tableFromJSON, tableToIPC } from 'apache-arrow';

const CRITICAL_ERROR_RE = /(getRawIndex|getDataParams|Cannot read properties of undefined.*getRawIndex|TreemapSeries)/i;
const SUPABASE_STORAGE_KEY = 'sb-dxlkejsrvuknuajkltwm-auth-token';
const FILE_ID = '06d713f2-f413-4ce0-89f7-be6add621255';
const TASK_ID = '44444444-4444-4444-8444-444444444444';
const PRESENTATION_ID = '55555555-5555-4555-8555-555555555555';
const REPORT_ID = '66666666-6666-4666-8666-666666666666';
const SAVED_TITLE = 'E2E Widget Crítico';

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

function buildGranularRows() {
  return [
    { almacen: 'Almacen Norte', stock_disponible: 140, material: 'MAT-001', fecha_de_stock: '2021-07-31' },
    { almacen: 'Almacen Sur', stock_disponible: 60, material: 'MAT-002', fecha_de_stock: '2021-07-31' },
    { almacen: 'Almacen Norte', stock_disponible: 120, material: 'MAT-003', fecha_de_stock: '2021-06-30' },
    { almacen: 'Almacen Sur', stock_disponible: 90, material: 'MAT-004', fecha_de_stock: '2021-06-30' },
  ];
}

function buildChartOption() {
  const granularArrow = encodeArrowBase64(buildGranularRows());

  return {
    title: { text: 'Top 2 almacenes principales' },
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
  };
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

async function installCriticalFlowMocks(page: Page): Promise<void> {
  const chartOption = buildChartOption();
  const reportsStore: any[] = [];
  const presentationsStore = [
    {
      id: PRESENTATION_ID,
      name: 'API-DatosPrueba_Final.xlsx',
      file_id: FILE_ID,
      created_at: '2026-04-13T12:00:00.000Z',
    },
  ];
  let taskResolved = false;

  await page.route('https://dxlkejsrvuknuajkltwm.supabase.co/rest/v1/uploaded_files**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ file_name: 'API-DatosPrueba_Final.xlsx' }),
    });
  });

  await page.route('http://localhost:8000/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;

    if (method === 'OPTIONS') {
      await route.fulfill({
        status: 200,
        headers: {
          'access-control-allow-origin': '*',
          'access-control-allow-methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
          'access-control-allow-headers': '*',
        },
        body: '',
      });
      return;
    }

    if (path === '/api/v1/chat' && method === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: [] }),
      });
      return;
    }

    if (path === `/api/v1/chat/${FILE_ID}` && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
      return;
    }

    if (path === '/api/v1/analyze' && method === 'POST') {
      taskResolved = false;
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ task_id: TASK_ID }),
      });
      return;
    }

    if (path === `/api/v1/tasks/${TASK_ID}` && method === 'GET') {
      taskResolved = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'completed',
          result: {
            analysis: 'Top 2 almacenes principales por stock disponible.',
            chart_options: [chartOption],
            recommendations: ['Revisar rotación en Almacen Sur.'],
          },
        }),
      });
      return;
    }

    if (path === '/api/v1/reports' && method === 'POST') {
      const payload = request.postDataJSON() as any;
      const report = {
        id: REPORT_ID,
        title: payload.title,
        file_id: payload.file_id,
        created_at: '2026-04-13T12:05:00.000Z',
        content: payload.content,
      };

      reportsStore.splice(0, reportsStore.length, report);

      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: [report] }),
      });
      return;
    }

    if (path === '/api/v1/reports' && method === 'GET') {
      const presentationId = url.searchParams.get('presentation_id');
      const reports = presentationId ? reportsStore.filter((report) => report.file_id === FILE_ID) : reportsStore;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(reports),
      });
      return;
    }

    if (path === '/api/v1/reports/layout' && method === 'PUT') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', updated: 1 }),
      });
      return;
    }

    if (path === '/api/v1/presentations' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(presentationsStore),
      });
      return;
    }

    await route.fallback();
  });
}

async function hoverChartSurface(page: Page, testId: string): Promise<void> {
  const surfaces = [
    `[data-testid="${testId}"] svg`,
    `[data-testid="${testId}"] canvas`,
    `[data-testid="${testId}"] [class*="echarts"]`,
    `[data-testid="${testId}"]`,
  ];

  for (const selector of surfaces) {
    const locator = page.locator(selector).first();
    const count = await locator.count();
    if (count === 0) continue;
    const visible = await locator.isVisible().catch(() => false);
    if (!visible) continue;
    await locator.hover();
    return;
  }

  throw new Error(`No se encontró superficie hover para ${testId}`);
}

async function clickWidgetChart(page: Page, testId: string): Promise<void> {
  const chartContainer = page.locator(`[data-testid="${testId}"]`);
  await expect(chartContainer).toBeVisible();

  const box = await chartContainer.boundingBox();
  if (!box) throw new Error(`No se pudo obtener bounding box de ${testId}`);

  const attempts: Array<[number, number]> = [
    [0.25, 0.45],
    [0.55, 0.40],
    [0.75, 0.48],
  ];

  for (const [fx, fy] of attempts) {
    const x = box.x + box.width * fx;
    const y = box.y + box.height * fy;
    await page.mouse.click(x, y);
    const clearButton = page.getByTestId('dashboard-clear-filters');
    const visible = await clearButton.isVisible().catch(() => false);
    if (visible) return;
    await page.waitForTimeout(200);
  }

  throw new Error(`No se pudo activar el filtro global desde ${testId}`);
}

test.describe('Critical chat -> save -> dashboard flow', () => {
  test('Prompt -> save widget -> dashboard -> click/filter -> hover -> clear -> reload', async ({ page }) => {
    const criticalErrors = trackCriticalErrors(page);

    await installMockSession(page);
    await installCriticalFlowMocks(page);

    await page.goto(`/?fileId=${FILE_ID}&__qa_chat=1&__qa_chat_token=playwright-token`);

    await expect(page.getByTestId('chat-input')).toBeVisible();
    await page.getByTestId('chat-input').fill('Muéstrame el top 2 de almacenes principales por stock disponible');
    await page.getByTestId('chat-submit').click();

    await expect(page.getByTestId('chart-save-button')).toBeVisible({ timeout: 10000 });
    await page.getByTestId('chart-save-button').click();

    await expect(page.getByTestId('save-report-dialog')).toBeVisible();
    await page.getByTestId('save-report-title-input').fill(SAVED_TITLE);
    await page.getByTestId('save-report-confirm').click();
    await expect(page.getByTestId('save-report-dialog')).toHaveCount(0);

    await page.goto('/dashboard?__qa_dashboard=1&__qa_dashboard_token=playwright-token');

    const widgetTestId = `dashboard-widget-${REPORT_ID}`;
    await expect(page.getByTestId(widgetTestId)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(SAVED_TITLE)).toBeVisible();

    await clickWidgetChart(page, widgetTestId);
    await expect(page.getByTestId('dashboard-clear-filters')).toBeVisible();

    await hoverChartSurface(page, widgetTestId);

    await page.getByTestId('dashboard-clear-filters').click();
    await expect(page.getByTestId('dashboard-clear-filters')).toHaveCount(0);

    await page.reload();
    await expect(page.getByTestId(widgetTestId)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(SAVED_TITLE)).toBeVisible();

    expect(criticalErrors).toEqual([]);
  });
});
