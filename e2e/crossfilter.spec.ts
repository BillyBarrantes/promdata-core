import { expect, Page, test } from '@playwright/test';

const CRITICAL_ERROR_RE = /(getRawIndex|getDataParams|Cannot read properties of undefined.*getRawIndex|TreemapSeries)/i;

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

async function openMenuByChartClick(page: Page, testId: string): Promise<void> {
  const chartContainer = page.locator(`[data-testid="${testId}"]`);
  await expect(chartContainer).toBeVisible();

  const box = await chartContainer.boundingBox();
  if (!box) throw new Error(`No se pudo obtener bounding box de ${testId}`);

  const attempts: Array<[number, number]> = [
    [0.22, 0.44],
    [0.5, 0.42],
    [0.75, 0.46],
  ];

  for (const [fx, fy] of attempts) {
    const x = box.x + box.width * fx;
    const y = box.y + box.height * fy;
    await page.mouse.click(x, y);

    const menuHeader = page.getByText('Sugerencias de Análisis');
    const visible = await menuHeader.isVisible().catch(() => false);
    if (visible) return;

    await page.waitForTimeout(150);
  }

  throw new Error(`No se pudo abrir el menú de drill-down desde ${testId}`);
}

async function openMenuByTrigger(page: Page, triggerTestId: string): Promise<void> {
  await page.getByTestId(triggerTestId).click();
  await expect(page.getByText('Sugerencias de Análisis')).toBeVisible();
}

async function applyFilterFromMenu(page: Page): Promise<void> {
  await page.getByRole('button', { name: /Filtrar aquí/i }).click();
}

async function assertFilterMatchesLastFocus(page: Page): Promise<void> {
  const activeFilter = page.getByTestId('active-filter');
  const lastFocus = page.getByTestId('last-click-category');
  await expect(activeFilter).not.toHaveText('none');
  await expect(activeFilter).toHaveText(await lastFocus.textContent());
}

async function hoverChartSurface(page: Page, testId: string): Promise<void> {
  const chartContainer = page.locator(`[data-testid="${testId}"]`).first();
  await expect(chartContainer).toBeVisible();

  const box = await chartContainer.boundingBox();
  if (!box) {
    throw new Error(`No se pudo obtener bounding box de ${testId}`);
  }

  const attempts: Array<[number, number]> = [
    [0.5, 0.72],
    [0.32, 0.68],
    [0.68, 0.68],
    [0.5, 0.58],
  ];

  for (const [fx, fy] of attempts) {
    const x = box.x + box.width * fx;
    const y = box.y + box.height * fy;
    await page.mouse.move(x, y);
    await page.waitForTimeout(100);
    return;
  }

  throw new Error(`No se encontró superficie hover para ${testId}`);
}

test.describe('Cross-filter hardening QA', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/qa/crossfilter');
    await expect(page.getByText('QA Cross-Filter Lab')).toBeVisible();
  });

  test('Treemap: click -> filter -> hover -> clear', async ({ page }) => {
    const criticalErrors = trackCriticalErrors(page);

    await openMenuByChartClick(page, 'chart-treemap');
    await applyFilterFromMenu(page);
    await assertFilterMatchesLastFocus(page);

    await hoverChartSurface(page, 'chart-treemap');

    await openMenuByChartClick(page, 'chart-treemap');
    await applyFilterFromMenu(page);
    await expect(page.getByTestId('active-filter')).toHaveText('none');
    expect(criticalErrors).toEqual([]);
  });

  test('Bar: click -> filter -> hover -> clear', async ({ page }) => {
    const criticalErrors = trackCriticalErrors(page);

    await openMenuByTrigger(page, 'trigger-bar-alpha');
    await applyFilterFromMenu(page);
    await assertFilterMatchesLastFocus(page);

    await hoverChartSurface(page, 'chart-bar');

    await openMenuByTrigger(page, 'trigger-bar-alpha');
    await applyFilterFromMenu(page);
    await expect(page.getByTestId('active-filter')).toHaveText('none');
    expect(criticalErrors).toEqual([]);
  });

  test('Line: click -> filter -> hover -> clear', async ({ page }) => {
    const criticalErrors = trackCriticalErrors(page);

    await openMenuByTrigger(page, 'trigger-line-beta');
    await applyFilterFromMenu(page);
    await assertFilterMatchesLastFocus(page);

    await hoverChartSurface(page, 'chart-line');

    await openMenuByTrigger(page, 'trigger-line-beta');
    await applyFilterFromMenu(page);
    await expect(page.getByTestId('active-filter')).toHaveText('none');
    expect(criticalErrors).toEqual([]);
  });
});
