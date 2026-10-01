import { expect, test } from '@playwright/test';

// [QW-4] Browser assertion: DuckDB-WASM bootstrap must succeed under the
// real CSP emitted by next.config.mjs (jsDelivr in connect-src).
// Before the fix, the initial fetch() to the CDN was CSP-blockable in
// production, killing cross-filter only in prod. This test fails if the
// engine cannot initialize in a real browser.

// Solo violaciones CSP que afectan al bootstrap de DuckDB: el fetch al CDN
// (connect-src) o la construcción del worker. Ruido preexistente y ajeno
// (ej. script de analytics bloqueado por script-src) no debe falsear el test.
const DUCKDB_CSP_RE = /(connect-src|cdn\.jsdelivr\.net|duckdb)/i;
const CSP_RE = /Content Security Policy|Refused to connect/i;

test.describe('DuckDB-WASM bootstrap (QW-4)', () => {
  test('engine initializes without CSP violations', async ({ page }) => {
    const cspErrors: string[] = [];

    page.on('console', (msg) => {
      const text = msg.text();
      if (msg.type() === 'error' && CSP_RE.test(text) && DUCKDB_CSP_RE.test(text)) {
        cspErrors.push(text);
      }
    });
    page.on('pageerror', (error) => {
      if (CSP_RE.test(error.message) && DUCKDB_CSP_RE.test(error.message)) {
        cspErrors.push(error.message);
      }
    });

    await page.goto('/qa/duckdb');
    await expect(page.getByTestId('duckdb-status')).toHaveText('ready', {
      timeout: 45_000, // WASM download + instantiate can be slow on first load
    });

    expect(cspErrors).toEqual([]);
  });
});
