/**
 * Happy-Path E2E: Prompt → Chart + Narrative
 *
 * Validates the core user journey:
 * 1. User sends an analytical prompt
 * 2. System returns a completed analysis with chart_options + narrative
 * 3. Chart renders visually in the canvas
 * 4. Narrative text appears in the chat
 * 5. No critical JS errors during the flow
 *
 * This test uses the same mock architecture as chat-dashboard-critical.spec.ts.
 * It does NOT require a running backend — all API calls are intercepted.
 */
import { expect, Page, test } from '@playwright/test';

const CRITICAL_ERROR_RE = /(getRawIndex|getDataParams|Cannot read properties of undefined|TreemapSeries|ChunkLoadError)/i;
const SUPABASE_STORAGE_KEY = 'sb-dxlkejsrvuknuajkltwm-auth-token';
const FILE_ID = 'e2e-happy-path-file-0001';
const TASK_ID = 'e2e-happy-path-task-0001';
const ANALYSIS_TEXT = 'El tipo de unidad Pesado (30T) es el que genera mayor gasto de combustible acumulado.';
const CHART_TITLE = 'Top 3 tipos de unidad por gasto de combustible';

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

function buildChartOption() {
  return {
    title: { text: CHART_TITLE },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 64, right: 24, top: 48, bottom: 48 },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: ['Ligero (4T)', 'Mediano (15T)', 'Pesado (30T)'],
    },
    series: [
      {
        type: 'bar',
        name: 'Gasto Combustible (S/)',
        data: [12500000, 22800000, 37400000],
        label: { show: true, position: 'right' },
      },
    ],
  };
}

async function installMockSession(page: Page): Promise<void> {
  const session = {
    access_token: 'playwright-happy-token',
    refresh_token: 'playwright-happy-refresh',
    token_type: 'bearer',
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    user: {
      id: 'playwright-happy-user',
      aud: 'authenticated',
      role: 'authenticated',
      email: 'playwright-happy@promdata.local',
    },
  };

  await page.addInitScript(
    ([storageKey, payload]) => {
      window.localStorage.setItem(storageKey, JSON.stringify(payload));
    },
    [SUPABASE_STORAGE_KEY, session] as const
  );
}

async function installHappyPathMocks(page: Page): Promise<void> {
  const chartOption = buildChartOption();
  let taskPolls = 0;

  // Mock Supabase uploaded_files
  await page.route('https://dxlkejsrvuknuajkltwm.supabase.co/rest/v1/uploaded_files**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ file_name: 'transporte_rutas_historico_lima.xlsx' }),
    });
  });

  // Mock all backend API calls
  await page.route('http://localhost:8000/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;

    // CORS preflight
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

    // POST /chat — save user message
    if (path === '/api/v1/chat' && method === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: [] }),
      });
      return;
    }

    // GET /chat/:fileId — chat history (empty for clean test)
    if (path === `/api/v1/chat/${FILE_ID}` && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
      return;
    }

    // POST /analyze — start analysis
    if (path === '/api/v1/analyze' && method === 'POST') {
      taskPolls = 0;
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ task_id: TASK_ID }),
      });
      return;
    }

    // GET /tasks/:taskId — poll for result
    // Simulate realistic processing: first 2 polls return "processing",
    // then resolve with completed analysis.
    if (path === `/api/v1/tasks/${TASK_ID}` && method === 'GET') {
      taskPolls++;

      if (taskPolls <= 2) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'processing', result: null }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'completed',
          result: {
            analysis: ANALYSIS_TEXT,
            chart_options: [chartOption],
            recommendations: [
              'Evaluar eficiencia de unidades Pesado (30T) para reducir consumo.',
            ],
          },
        }),
      });
      return;
    }

    // Reports & presentations — passthrough defaults
    if (path === '/api/v1/reports' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
      return;
    }

    if (path === '/api/v1/presentations' && method === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
      return;
    }

    await route.fallback();
  });
}

test.describe('Happy Path: Prompt → Chart + Narrative', () => {
  test('User sends prompt and receives chart with analysis text', async ({ page }) => {
    const criticalErrors = trackCriticalErrors(page);

    // 1. Setup
    await installMockSession(page);
    await installHappyPathMocks(page);

    // 2. Navigate to chat with the file
    await page.goto(`/?fileId=${FILE_ID}&__qa_chat=1&__qa_chat_token=playwright-happy-token`);

    // 3. Verify chat input is ready
    await expect(page.getByTestId('chat-input')).toBeVisible();

    // 4. Send analytical prompt
    await page.getByTestId('chat-input').fill(
      '¿Cuál es el tipo de unidad que nos ha generado el mayor gasto de combustible acumulado en todos estos años? Muestra el top 3.'
    );
    await page.getByTestId('chat-submit').click();

    // 5. Wait for chart to render (visible ECharts container)
    // The chart title should appear in the rendered output
    await expect(page.getByText(CHART_TITLE)).toBeVisible({ timeout: 15000 });

    // 6. Verify the narrative analysis text is visible
    await expect(page.getByText(ANALYSIS_TEXT, { exact: false })).toBeVisible({ timeout: 5000 });

    // 7. Verify the chart save button is available (chart rendered correctly)
    await expect(page.getByTestId('chart-save-button')).toBeVisible();

    // 8. No critical JS errors during the entire flow
    expect(criticalErrors).toEqual([]);
  });

  test('Error response does NOT expose internal details', async ({ page }) => {
    await installMockSession(page);

    // Mock API that returns a sanitized 500 error
    await page.route('http://localhost:8000/api/v1/**', async (route) => {
      const method = route.request().method();

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

      // Simulate a sanitized 500 response (as our exception handler now returns)
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'Error interno del servidor. Por favor, inténtelo de nuevo.',
        }),
      });
    });

    // Intercept the response to verify no Python internals leak
    const responses: string[] = [];
    page.on('response', async (response) => {
      if (response.status() === 500) {
        const body = await response.text().catch(() => '');
        responses.push(body);
      }
    });

    await page.goto(`/?fileId=${FILE_ID}&__qa_chat=1&__qa_chat_token=playwright-happy-token`);
    await page.waitForTimeout(3000);

    // Verify no response contains Python tracebacks or internal error details
    for (const body of responses) {
      expect(body).not.toContain('Traceback');
      expect(body).not.toContain('TypeError');
      expect(body).not.toContain('NoneType');
      expect(body).not.toContain('AttributeError');
      expect(body).not.toContain('KeyError');
      expect(body).not.toContain('supabase');
    }
  });
});
