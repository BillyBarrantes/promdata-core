# Paquete de Handoff Técnico para Open Design — PromData V1

> **Audiencia:** Equipo de Diseño (Open Design), Desarrolladores Frontend, Arquitectos de Producto.  
> **Propósito:** Especificación técnica vinculante, inventario de contratos visuales, restricciones de rendimiento y matriz de estados para el diseño de la interfaz de **PromData V1 Pública**.  
> **Versión:** 1.0 (Fase 0 Baseline)

---

## 1. Visión y Dirección Estética

PromData V1 se posiciona como una **herramienta analítica de grado empresarial** para analistas, CFOs y directivos.
La estética visual no debe ser la de un "chatbot informal", sino la de una **consola de inteligencia de datos moderna, sobria y de alta densidad informativa**, inspirada en la elegancia técnica de **Cursor**, **Linear** y **ThoughtSpot**:

- **Tema Primario:** Dark Theme refinado (superficies neutras de carbón/pizarra `#090d16`, `#111827`, `#1f2937`), con soporte de Light Theme de alto contraste.
- **Tipografía:**
  - Texto de interfaz y narrativa: Sans-serif limpia (Geist, Inter o SF Pro).
  - Cifras numéricas, códigos, SQL y KPIs: Monoespaciada tabular (Geist Mono, JetBrains Mono o SF Mono) con alineación numérica a la derecha.
- **Micro-interacciones:** Transiciones fluidas (<150ms), bordes sutiles con translucidez (`border-white/10`), sombras suaves y cero gradientes estridentes.

---

## 2. Inventario de Rutas (Next.js 14 App Router)

| Ruta | Nombre de Pantalla | Función Principal | Componentes Clave |
|---|---|---|---|
| `/` | Landing & Portal | Entrada, presentación de valor, acceso directo a iniciar sesión. | Hero, Value Proposition, Pricing Preview, CTA de Ingreso. |
| `/cargar-datos` | Centro de Carga | Subida de archivos CSV/Excel, arrastrar y soltar, selector de hojas. | UploadZone con progreso, FileValidator, SheetSelector, Historial de archivos. |
| `/chat/[file_id]` | Consola Analítica | Espacio principal de análisis conversacional, dashboards y tablas. | Sidebar, ChatStream, WorkspaceCanvas, EChartsContainer, SmartTable, EvidenceDrawer. |
| `/conocimiento` | Base Documental | Búsqueda y preguntas sobre documentos PDF institucionales (RAG). | DocumentList, DocumentUploader, KnowledgeChat, CitationsPanel. |
| `/billing` | Suscripciones | Gestión de cuotas, plan de suscripción B2C y facturación. | PlanCards, UsageMeters, StripeCheckoutButton, InvoiceHistory. |

---

## 3. Matriz de Estados de Ciclo de Vida del Análisis

El diseño de la UI debe contemplar explícitamente cada uno de estos estados para evitar pantallas congeladas o vacías:

```
[ IDLE ] ──(Usuario envía prompt)──► [ UPLOADING / PROFILING ]
                                              │
                                              ▼
                                       [ PLANNING (LLM) ]
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    ▼                                                   ▼
        [ CLARIFICATION_REQUIRED ]                               [ PROCESSING (Ibis) ]
      (Pregunta ambigua: 2+ opciones)                                   │
                    │                                                   ▼
                    │ (Usuario elige opción)                     [ COMPLETED (200) ]
                    └────────────────────────────────► (Renderiza KPIs + ECharts + Narrativa)
                                                                        │
                                              ┌─────────────────────────┴─────────────────────────┐
                                              ▼                                                   ▼
                                     [ CROSS-FILTER LOCAL ]                      [ CROSS-FILTER SERVER ]
                                      (Snapshot <= 10K filas)                     (Snapshot > 10K filas)
                                    "⚡ Instantáneo · En cliente"                 "⏳ Recalculando sobre N filas..."
```

| Estado | Evento Desencadenante | Respuesta Visual Requerida |
|---|---|---|
| `idle` | Estado inicial tras cargar dataset. | Sugerencias de análisis rápido ("Plantillas sugeridas"), input de chat listo. |
| `profiling` | Ingesta y lectura de tipos de datos. | Skeleton loader discreto: *"Reconociendo columnas y tipos de datos..."* |
| `planning` | LLM traduciendo a `AnalysisPlan`. | Indicador de pensamiento con pulso: *"Interpretando intención de negocio..."* |
| `clarification_required` | Columna o métrica ambigua. | Card interactiva de aclaración con botones de selección de columnas candidatas. |
| `blocked` | Métrica no encontrada o agregación inválida. | Banner didáctico de bloqueo explicando la causa y sugiriendo columnas válidas. |
| `processing` | Ejecución en Celery + Ibis/DuckDB. | Barra de progreso con tiempo transcurrido: *"Calculando métricas sobre DuckDB..."* |
| `completed` | Análisis finalizado con éxito. | Entrada al lienzo: KPI Cards + Gráficos ECharts + Narrativa con botón de evidencia. |
| `failed` | Error recuperable o sintaxis corrupta. | Alerta con opción *"Reintentar"* y explicación amigable sin exponer stacktraces. |
| `timeout` | Tarea excede 300 segundos. | Aviso de límite de tiempo con sugerencia de acotar el filtro temporal. |

---

## 4. Anatomía de Componentes Analíticos

### 4.1 Header del Análisis
- **Título de la Consulta:** Resumen normalizado de la pregunta del usuario.
- **Badge de Estado de Datos:** e.g., `Snapshot completo (100% datos)` o `Muestra de 10K filas`.
- **Badges de Filtro Base:** Chips que muestran los filtros aplicados por el backend (e.g. `Tipo Movimiento="Ingreso"`).
- **Botón "Ver Evidencia":** Abre el `EvidenceDrawer` con el hash del plan y los facts calculados.

### 4.2 KPI Cards
- **Valor Principal:** Tipografía monoespaciada de gran tamaño (e.g., `$1,561,828.66`).
- **Delta / Comparativa:** Chip verde (+15.4%) o rojo (-4.2%) con flecha de tendencia.
- **Subtítulo:** Período de referencia (e.g., `Total año 2024` o `Último corte 2024-04-30`).
- **Badge de Advertencia:** Si la métrica es derivada o snapshot (e.g., `Evaluado al último corte`).

### 4.3 Contenedor de ECharts
- **Altura estándar:** 380px a 460px según densidad de datos.
- **Tooltips:** Formato dark con sombra, valores formateados con separador de miles y moneda detectada.
- **Badge Interactivo:** `⚡ Clic en una barra para filtrar` visible antes de interactuar.
- **Responsive:** Redibujado automático con `ResizeObserver` sin parpadeos.

### 4.4 Drawer de Evidencia (`EvidenceBundleV1`)
Un panel lateral deslizable (Right Drawer de 480px de ancho) que muestra:
1. **Identificador único:** `Evidence ID: ev-8f9a2b1c`.
2. **Hash del Plan:** `Plan Hash: sha256:7c9e...` garantizando reproducibilidad.
3. **Métricas Calculadas:** Lista de pares clave-valor calculados por DuckDB.
4. **Consulta Canónica:** Expresión SQL / Ibis compilada que produjo los números.
5. **Timestamp de Ejecución:** Fecha y hora UTC exacta de cómputo.

---

## 5. Restricciones Técnicas Inmutables (OpenCode Compliance)

Cualquier diseño entregado por Open Design debe respetar estas **3 leyes físicas del sistema**:

1. **Límite de DuckDB-WASM en Cliente (10,000 filas):**
   - El navegador solo almacena hasta 10,000 filas en formato Apache Arrow por motivos de memoria móvil y rendimiento.
   - Si un gráfico representa un dataset mayor a 10K filas, el filtrado interactivo (cross-filter) **no puede simularse en cliente**. La UI debe mostrar el estado *"Recalculando sobre N filas..."* y disparar la petición al backend.
2. **Preservación de `chart_base_filters`:**
   - Si un gráfico se construyó con un filtro base (e.g., `canal = 'E-commerce'`), y el usuario hace clic en un mes (e.g., `'2024-05'`), el filtro final es la suma: `canal='E-commerce' + fecha='2024-05'`. El diseño debe mostrar ambos filtros en la barra superior.
3. **Forecast como "Experimental":**
   - Todo gráfico de pronóstico debe llevar obligatoriamente el tag *"Experimental"* y mostrar la banda de intervalo de confianza (área sombreada semitransparente).

---

## 6. Paleta Semántica de Colores (Tokens)

```css
/* Paleta Semántica PromData V1 */
--surface-background:   #090d16;  /* Fondo principal canvas */
--surface-card:         #111827;  /* Fondo de tarjetas y widgets */
--surface-card-hover:   #1f2937;  /* Hover de tarjetas */
--border-subtle:        rgba(255, 255, 255, 0.08); /* Bordes de widgets */

/* Colores Semánticos */
--semantic-success:     #10b981;  /* Crecimiento positivo, conciliado */
--semantic-warning:     #f59e0b;  /* Stock crítico, dataset truncado */
--semantic-error:       #ef4444;  /* Bloqueo, métrica no encontrada */
--semantic-info:        #3b82f6;  /* Notas explicativas, filtros activos */
--semantic-experimental:#8b5cf6;  /* Pronósticos, proyecciones IA */

/* Paleta de Series ECharts (Accesible) */
--chart-series-1:       #38bdf8;  /* Azul cielo primario */
--chart-series-2:       #818cf8;  /* Índigo */
--chart-series-3:       #34d399;  /* Esmeralda */
--chart-series-4:       #fbbf24;  /* Ámbar */
--chart-series-5:       #f87171;  /* Coral */
--chart-series-6:       #a78bfa;  /* Violeta */
--chart-series-7:       #38bdf8;  /* Cian */
--chart-series-8:       #94a3b8;  /* Gris pizarra para 'Otros' */
```
