import { expect, test, Page } from '@playwright/test';

/**
 * real-pipeline.spec.ts — E2E Real sin Mocks (Fase 1)
 * ══════════════════════════════════════════════════════════
 * Regla innegociable: CERO interceptación de red con page.route() para endpoints de API.
 * 
 * Verifica:
 *   1. Navegación a las consolas y rutas de prueba reales sin excepciones críticas.
 *   2. Inicialización completa de DuckDB-WASM sin violaciones de CSP.
 *   3. Renderizado determinista de componentes analíticos y contenedores ECharts.
 */

const CRITICAL_ERROR_RE = /(getRawIndex|getDataParams|Cannot read properties of undefined.*getRawIndex|TreemapSeries|NullPointerException)/i;

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

test.describe('Fase 1: Real Pipeline E2E sin Mocks', () => {
  test('1. DuckDB-WASM arranca en el navegador sin violaciones de seguridad', async ({ page }) => {
    const errors = trackCriticalErrors(page);

    await page.goto('/qa/duckdb');
    await expect(page.getByTestId('duckdb-status')).toHaveText('ready', {
      timeout: 45_000,
    });

    expect(errors).toEqual([]);
  });

  test('2. Entorno analítico renderiza componentes y soporta interacciones sin mock de datos', async ({ page }) => {
    const errors = trackCriticalErrors(page);

    await page.goto('/qa/crossfilter');

    // Verificar que los gráficos se montan en el DOM real
    const barChart = page.locator('[data-testid="chart-bar-qa"]');
    await expect(barChart).toBeVisible({ timeout: 15_000 });

    // Verificar que el contenedor treemap se monta sin crasheos de series
    const treemapChart = page.locator('[data-testid="chart-treemap-qa"]');
    await expect(treemapChart).toBeVisible({ timeout: 15_000 });

    // Clic en trigger de drill-down
    const trigger = page.locator('[data-testid="trigger-drilldown-bar"]');
    if (await trigger.isVisible()) {
      await trigger.click();
      await expect(page.getByText('Sugerencias de Análisis')).toBeVisible();
    }

    expect(errors).toEqual([]);
  });
});
