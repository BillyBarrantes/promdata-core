---
version: alpha
name: PromData Dark Mode v2
description: Sistema visual nocturno para PromData. Neutraliza el fondo, dedica un plato al gráfico y lleva la distinción de series a la luminancia en vez del matiz.
colors:
  surface-base: "#0A0B0D"
  surface-inset: "#0E1014"
  surface-raised: "#14161A"
  surface-raised-2: "#181B20"
  surface-overlay: "#1E2227"
  scrim: "rgba(5,6,8,0.72)"
  text-primary: "#E8EAED"
  text-secondary: "#A8AEB8"
  text-muted: "#7C838F"
  text-faint: "#5A6270"
  border-subtle: "rgba(255,255,255,0.055)"
  border-default: "rgba(255,255,255,0.10)"
  border-strong: "rgba(255,255,255,0.16)"
  accent: "oklch(0.70 0.150 268)"
  accent-strong: "oklch(0.76 0.150 268)"
  on-accent: "#0A0B0D"
  success: "oklch(0.78 0.140 154)"
  danger: "oklch(0.71 0.160 25)"
  warning: "oklch(0.81 0.130 78)"
  series-1: "oklch(0.80 0.125 258)"
  series-2: "oklch(0.90 0.095 238)"
  series-3: "oklch(0.72 0.055 222)"
  series-4: "oklch(0.66 0.135 276)"
  series-5: "oklch(0.55 0.140 294)"
  series-6: "oklch(0.46 0.115 310)"
typography:
  family-sans: "ui-sans-serif, -apple-system, Segoe UI Variable Text, system-ui"
  family-mono: "ui-monospace, SF Mono, JetBrains Mono, Cascadia Mono, Menlo"
  scale:
    display: "clamp(1.63rem, 4vw, 2.75rem) / 1.06 / -0.035em"
    h2: "clamp(1.19rem, 2.2vw, 1.56rem) / 1.2 / -0.02em"
    h3: "0.9rem / 1.35 / -0.01em"
    body: "0.875rem / 1.5 / -0.005em"
    small: "0.78rem / 1.5"
    caption: "0.72rem / 1.5"
    mono: "tabular-nums, tabular figures siempre en cifras"
  weight: { regular: 400, medium: 500, semibold: 600 }
rounded:
  card: "16px"
  plate: "12px"
  sm: "8px"
  chip: "999px"
spacing:
  base: "4px"
  gap: "14px (9px en compacta)"
  pad-card: "20px (13px en compacta)"
  row-h: "40px (31px en compacta)"
  section: "44px entre secciones"
components:
  pd-card: "surface-raised + shadow-raised + highlight inset 5%. Sin borde."
  pd-plate: "surface inset para cualquier gráfico o tabla. hairline 5.5% + highlight 4% arriba."
  pd-overlay: "surface-overlay + blur(18px) saturate(1.3) + shadow-overlay. Único lugar con glass."
  pd-num: "cifras tabulares. Obligatorio en KPI, tabla, ticks y tooltips."
  chart-line: "grosor 2px, linejoin redondeado, glow del propio color al 34%."
  chart-grid: "solo horizontal, 4% de alpha, 1px. La línea cero sube a 10%."
  kpi-card: "sin borde, superficie raised, valor mono, delta con flecha y color semántico."
  smart-table: "superficie inset, hairline 10% entre filas, hover por fila, sin zebra."
  chat-panel: "máximo 28% del ancho, colapsable, mensajes en superficie neutra."
---

## Overview

PromData procesa archivos de ventas y promociones y devuelve narrativas con gráficos. En el modo nocturno v1 el problema no era el color del fondo: era que **el gráfico no tenía escenario propio**. Compartía su tono con la card, la retícula pesaba más que las series, los trazos de 1 px se diluían por antialiasing sobre fondo oscuro y las cinco o seis series se distinguían solo por matiz — con luminancias tan parecidas que en gris colapsaban.

El sistema v2 resuelve eso en tres decisiones:

1. **Un plato para los datos.** Toda visualización vive sobre `--surface-inset` (#0E1014), una superficie con 4% más de luminancia que la base y un paso *por debajo* de la card que la contiene, con hairline al 5.5% y highlight especular. El gráfico deja de flotar.
2. **La distinción migra al tono de luz.** Las seis series comparten un arco cromático de 90° y se separan por luminancia (L 0.90 → 0.46, ΔL ≥ 0.06), patrón de trazo y forma de marcador. Ningún dato depende del color como canal único.
3. **La profundidad se compra con luminancia.** La sombra queda para el contacto; la jerarquía la hace la escalera base → inset → raised → overlay → scrim. El glass existe solo en overlay.

### Reglas no negociables

- Negro y blanco puros no existen. Fondo más profundo `#0A0B0D`, texto más claro `#E8EAED`.
- Texto pequeño (≤ 0.875rem) nunca por debajo de 4.5:1. Ticks de eje en `--text-muted`: 5.0:1 sobre el plato.
- Trazo de serie mínimo 2 px. Marcas no textuales (series, barras) nunca por debajo de 3:1; `--series-6` es el piso del sistema.
- Máximo seis series. Con siete o más, se agrupa en "Otros" y el séptimo color no se inventa.
- Máximo un acento de interfaz y un arco de datos por vista.
- Radio anidado: hijo ≤ padre (16 → 12 → 8).
- Una sola capa de glass por vista, y solo si hay contenido debajo que se pueda difuminar.
- Sombras de dos capas (ambiente + contacto). Una sombra sola se lee falsa.
- Retícula horizontal únicamente. La vertical solo existe si hay más de 14 categorías temporales y sin etiquetas propias.
- Cada gráfico lleva una línea de lectura en lenguaje natural ("Lectura") debajo, no solo eje y leyenda.

### Componentes base

| Componente | Superficie | Notas |
|---|---|---|
| `kpi-card` | raised | Sin borde. Valor mono, delta con flecha, sparkline al 10% de alpha. |
| `chart-card` | raised + plato inset | Cabecera, plato, leyenda con valor final y delta, línea de lectura. |
| `smart-table` | inset | Hairline 10%, hover por fila, tendencia en sparkline, cifras mono. |
| `chat-panel` | raised | 28% del ancho máximo, colapsable a 64 px, fuentes citadas en mono. |
| `pd-overlay` | overlay | Tooltips, menús, drawer. Blur 18px + hairline + dos sombras. |

### Contraste medido

| Par | Ratio |
|---|---|
| `--text-primary` sobre `--surface-base` | 16.8:1 |
| `--text-secondary` sobre `--surface-raised` | 8.1:1 |
| `--text-muted` sobre `--surface-inset` | 5.0:1 (AA en texto pequeño) |
| Etiquetas de eje v1 (`rgba(255,255,255,.35)`) | 3.1:1 ✕ |
| `--series-6` (piso) sobre plato | 3.2:1 (≥ 3:1 para marcas) |
| `--on-accent` sobre `--accent` | 9.0:1 |

### Alcance

Solo capa de presentación: `app/globals.css`, `tailwind.config.ts` y `components/*`. El motor de datos (Ibis + DuckDB), los guards, el traductor semántico V3, la caché de respuestas, los workers Celery y las migraciones de Supabase quedan fuera de alcance por indicación explícita.

### Archivos de esta entrega

- `App.jsx` — estudio de refinamiento: dashboard nocturno, laboratorio antes/después, escalera de superficies, paleta de series y tabla de tokens.
- `promdata-dark.tokens.css` — bloque listo para pegar en `app/globals.css`.
- `handoff-modo-nocturno.md` — diagnóstico, cambios por archivo y criterios de aceptación.
