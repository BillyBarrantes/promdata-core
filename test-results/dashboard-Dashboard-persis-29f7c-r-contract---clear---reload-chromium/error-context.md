# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: dashboard.spec.ts >> Dashboard persisted cross-filter >> Persisted widgets: click -> filter -> infer contract -> clear -> reload
- Location: e2e/dashboard.spec.ts:193:7

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByTestId('dashboard-widget-22222222-2222-4222-8222-222222222222').getByRole('button', { name: /Ver Tabla/i })
Expected: visible
Timeout: 10000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 10000ms
  - waiting for getByTestId('dashboard-widget-22222222-2222-4222-8222-222222222222').getByRole('button', { name: /Ver Tabla/i })

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - generic [ref=e2]:
    - generic [ref=e4]:
      - generic [ref=e6]:
        - generic [ref=e8]: P
        - generic [ref=e9]: PromData
      - navigation [ref=e10]:
        - list [ref=e11]:
          - listitem [ref=e12]:
            - button "Inicio" [ref=e13]:
              - img
              - generic [ref=e14]: Inicio
          - listitem [ref=e15]:
            - button "Dashboard" [ref=e16]:
              - img
              - generic [ref=e17]: Dashboard
          - listitem [ref=e18]:
            - button "Cargar Datos" [ref=e19]:
              - img
              - generic [ref=e20]: Cargar Datos
          - listitem [ref=e21]:
            - button "Conocimiento" [ref=e22]:
              - img
              - generic [ref=e23]: Conocimiento
          - listitem [ref=e24]:
            - button "Glosario" [ref=e25]:
              - img
              - generic [ref=e26]: Glosario
      - generic [ref=e27]:
        - navigation [ref=e28]:
          - list [ref=e29]:
            - listitem [ref=e30]:
              - button "Modo oscuro Modo oscuro" [ref=e31]:
                - img "Modo oscuro" [ref=e32]
                - generic [ref=e33]: Modo oscuro
            - listitem [ref=e34]:
              - button "Extraer" [ref=e35]:
                - img
                - generic [ref=e36]: Extraer
        - button "Abrir menú de usuario" [ref=e38]:
          - generic [ref=e40]: MC
          - generic [ref=e41]:
            - paragraph [ref=e42]: Mi cuenta
            - paragraph [ref=e43]: Sin correo visible
    - main [ref=e44]:
      - generic [ref=e45]:
        - generic [ref=e46]:
          - heading "Tablero de Control" [level=1] [ref=e47]
          - paragraph [ref=e48]: Visión integral de tus indicadores clave.
        - generic [ref=e49]:
          - generic [ref=e50]:
            - img [ref=e51]
            - combobox [ref=e54]:
              - option "Todas (Lienzo Global Legacy)"
              - option "QA Presentacion Persistida" [selected]
            - generic:
              - img
          - button "Crear presentación" [ref=e55]:
            - img
          - button "Renombrar presentación" [ref=e56]:
            - img
          - button "Duplicar presentación" [ref=e57]:
            - img
          - button "Eliminar presentación" [ref=e58]:
            - img
          - button "Biblioteca" [ref=e59]:
            - img
            - text: Biblioteca
          - button "Resumen Ejecutivo" [ref=e60]:
            - img
            - text: Resumen Ejecutivo
          - button "Presentar" [ref=e61]
          - button "Actualizar datos" [ref=e62]:
            - img
      - generic [ref=e65]:
        - generic [ref=e67]:
          - generic [ref=e68]:
            - generic [ref=e69]:
              - generic [ref=e70]: Evolución del Stock Disponible
              - paragraph [ref=e71]: 13 de abr, 26
            - generic [ref=e72]:
              - button "Continuar en Chat" [ref=e73]:
                - img
              - button "Eliminar" [ref=e74]:
                - img
              - img [ref=e76]
          - generic [ref=e87]:
            - generic [ref=e89]:
              - generic [ref=e90]:
                - button "Tabla" [ref=e91]:
                  - img
                  - text: Tabla
                - button "Híbrida" [ref=e92]:
                  - img
                  - text: Híbrida
                - button "Gráfico" [ref=e93]:
                  - img
                  - text: Gráfico
              - button "Visual" [disabled]:
                - img
                - text: Visual
            - img [ref=e97]:
              - generic [ref=e99]:
                - generic [ref=e100]: "0"
                - generic [ref=e101]: "50"
                - generic [ref=e102]: "100"
                - generic [ref=e103]: "150"
                - generic [ref=e104]: "200"
                - generic [ref=e105]: "250"
                - generic [ref=e106]: Mar-2021
                - generic [ref=e107]: Apr-2021
                - generic [ref=e108]: May-2021
        - generic [ref=e124]:
          - generic [ref=e125]:
            - generic [ref=e126]:
              - generic [ref=e127]: Top 2 almacenes principales
              - paragraph [ref=e128]: 13 de abr, 26
            - generic [ref=e129]:
              - button "Continuar en Chat" [ref=e130]:
                - img
              - button "Eliminar" [ref=e131]:
                - img
              - img [ref=e133]
          - generic [ref=e141]:
            - generic [ref=e143]:
              - button "Visual" [disabled]:
                - img
                - text: Visual
            - img [ref=e147]:
              - generic [ref=e149]:
                - generic [ref=e150]: Almacen Norte
                - generic [ref=e151]: Almacen Sur
                - generic [ref=e152]: "0"
                - generic [ref=e153]: "30"
                - generic [ref=e154]: "60"
                - generic [ref=e155]: "90"
                - generic [ref=e156]: "120"
                - generic [ref=e157]: "150"
                - generic [ref=e160]: "140"
                - generic [ref=e161]: "60"
  - region "Notifications alt+T"
  - button "Open Next.js Dev Tools" [ref=e175] [cursor=pointer]:
    - img [ref=e176]
  - alert [ref=e179]
```

# Test source

```ts
  113 |           aggregation: 'sum',
  114 |           dimension: 'almacen',
  115 |           metric: 'stock_disponible',
  116 |           limit: 2,
  117 |         },
  118 |       },
  119 |     },
  120 |   };
  121 | 
  122 |   return [hybridReport, barReport];
  123 | }
  124 | 
  125 | async function installMockSession(page: Page): Promise<void> {
  126 |   const session = {
  127 |     access_token: 'playwright-token',
  128 |     refresh_token: 'playwright-refresh',
  129 |     token_type: 'bearer',
  130 |     expires_in: 3600,
  131 |     expires_at: Math.floor(Date.now() / 1000) + 3600,
  132 |     user: {
  133 |       id: 'playwright-user',
  134 |       aud: 'authenticated',
  135 |       role: 'authenticated',
  136 |       email: 'playwright@promdata.local',
  137 |     },
  138 |   };
  139 | 
  140 |   await page.addInitScript(
  141 |     ([storageKey, payload]) => {
  142 |       window.localStorage.setItem(storageKey, JSON.stringify(payload));
  143 |     },
  144 |     [SUPABASE_STORAGE_KEY, session] as const
  145 |   );
  146 | }
  147 | 
  148 | async function mockDashboardApis(page: Page): Promise<void> {
  149 |   const reports = buildDashboardReports();
  150 | 
  151 |   await page.route('http://localhost:8000/api/v1/presentations**', async (route) => {
  152 |     await route.fulfill({
  153 |       status: 200,
  154 |       contentType: 'application/json',
  155 |       body: JSON.stringify([
  156 |         {
  157 |           id: PRESENTATION_ID,
  158 |           name: 'QA Presentacion Persistida',
  159 |           file_id: FILE_ID,
  160 |           created_at: '2026-04-13T08:00:00.000Z',
  161 |         },
  162 |       ]),
  163 |     });
  164 |   });
  165 | 
  166 |   await page.route('http://localhost:8000/api/v1/reports/layout', async (route) => {
  167 |     await route.fulfill({
  168 |       status: 200,
  169 |       contentType: 'application/json',
  170 |       body: JSON.stringify({ ok: true }),
  171 |     });
  172 |   });
  173 | 
  174 |   await page.route('http://localhost:8000/api/v1/reports**', async (route) => {
  175 |     if (route.request().method() !== 'GET') {
  176 |       await route.fulfill({
  177 |         status: 200,
  178 |         contentType: 'application/json',
  179 |         body: JSON.stringify({ ok: true }),
  180 |       });
  181 |       return;
  182 |     }
  183 | 
  184 |     await route.fulfill({
  185 |       status: 200,
  186 |       contentType: 'application/json',
  187 |       body: JSON.stringify(reports),
  188 |     });
  189 |   });
  190 | }
  191 | 
  192 | test.describe('Dashboard persisted cross-filter', () => {
  193 |   test('Persisted widgets: click -> filter -> infer contract -> clear -> reload', async ({ page }) => {
  194 |     const criticalErrors = trackCriticalErrors(page);
  195 |     const inferredContractLogs: string[] = [];
  196 | 
  197 |     page.on('console', (msg) => {
  198 |       const text = msg.text();
  199 |       if (text.includes('🧠 [DASHBOARD] Query contract inferido para widget reactivo')) {
  200 |         inferredContractLogs.push(text);
  201 |       }
  202 |     });
  203 | 
  204 |     await installMockSession(page);
  205 |     await mockDashboardApis(page);
  206 | 
  207 |     await page.goto('/dashboard?__qa_dashboard=1&__qa_dashboard_token=playwright-token');
  208 | 
  209 |     await expect(page.getByTestId(`dashboard-widget-${HYBRID_REPORT_ID}`)).toBeVisible();
  210 |     await expect(page.getByTestId(`dashboard-widget-${BAR_REPORT_ID}`)).toBeVisible();
  211 |     await expect(
  212 |       page.getByTestId(`dashboard-widget-${HYBRID_REPORT_ID}`).getByRole('button', { name: /Ver Tabla/i })
> 213 |     ).toBeVisible();
      |       ^ Error: expect(locator).toBeVisible() failed
  214 | 
  215 |     await page.evaluate(() => {
  216 |       window.dispatchEvent(new CustomEvent('promdata:qa-dashboard-filter', {
  217 |         detail: { category: 'Almacen Norte' },
  218 |       }));
  219 |     });
  220 | 
  221 |     await expect(page.getByTestId('dashboard-clear-filters')).toBeVisible();
  222 |     await expect
  223 |       .poll(() => inferredContractLogs.length, { timeout: 10000 })
  224 |       .toBeGreaterThan(0);
  225 | 
  226 |     await page.getByTestId('dashboard-clear-filters').click();
  227 |     await expect(page.getByTestId('dashboard-clear-filters')).toHaveCount(0);
  228 | 
  229 |     await page.reload();
  230 |     await expect(page.getByTestId(`dashboard-widget-${HYBRID_REPORT_ID}`)).toBeVisible();
  231 |     await expect(page.getByTestId(`dashboard-widget-${BAR_REPORT_ID}`)).toBeVisible();
  232 | 
  233 |     expect(criticalErrors).toEqual([]);
  234 |   });
  235 | });
  236 | 
```