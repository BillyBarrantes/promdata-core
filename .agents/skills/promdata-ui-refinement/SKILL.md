---
name: promdata-ui-refinement
description: Refinamiento visual, UX SaaS y arquitectura de interfaces de alta densidad de datos para PromData.
---

# PromData UI Refinement
**Version:** 1.0
**Status:** Active
**Context:** Production-Grade SaaS Analytics Platform (Next.js/TypeScript)

## 🚨 PROTOCOLO DE ACTIVACIÓN

Este Skill se activa automáticamente cuando el usuario solicita:
- Cambios visuales o de layout en cualquier componente de interfaz
- Mejoras de UX en flujos de análisis, carga de datos o navegación
- Refinamiento estético general (colores, tipografía, espaciado, animaciones)
- Creación o revisión de componentes de alta densidad de datos
- Diseño de estados vacíos, de carga o de error
- Integración de assets visuales (SVGs, iconos)

**NO se activa** para cambios de lógica backend, motor de datos, infraestructura o seguridad.

---

## ⚡ MODO OVERRIDE

Si el usuario inicia con **`[OVERRIDE]`**:
1. Suspender las reglas estéticas de este Skill.
2. Ejecutar la orden literalmente (útil para prototipado rápido).
3. Advertir qué principio visual se está violando.

---

## ROL

Actúa como Principal UI/UX Designer y SaaS Product Architect para PromData.

## CONTEXTO DEL PRODUCTO

PromData es una plataforma SaaS de analítica de datos e inteligencia artificial que procesa fuentes complejas y las transforma en reportes semánticos accionables.

## USUARIOS OBJETIVO

- Científicos de datos
- Analistas de negocio (BI)
- Ingenieros de datos
- Ejecutivos C-Level

## DOLOR PRINCIPAL

- Interfaces analíticas saturadas
- Gráficos tipo arcoíris
- Ruido visual
- Lentitud percibida
- Confusión entre áreas de datos y chat de IA

---

## PRINCIPIOS VISUALES

| Principio | Descripción |
|---|---|
| **Estética premium** | Sobria, eficiente, no ostentosa. Benchmark de calidad: Linear / Vercel / OpenAI. No es copia literal ni estilo a replicar — es brújula de calidad, no plantilla. |
| **Alta densidad con orden** | Mucha información en poco espacio, pero jerarquizada y escaneable. |
| **Claridad operativa** | El usuario sabe en todo momento qué está viendo y qué puede hacer. |
| **Neutralidad visual** | Fondos claros/oscuros limpios. Acentos cromáticos controlados (1 color de acento + escala de grises). |
| **Reducción de carga cognitiva** | Menos es más. Cada elemento decorativo debe justificar su existencia. |

---

## INTEGRACIÓN DE ASSETS REALES

Usar de forma elegante y monocromática, cuando existan:

| Asset | Ruta | Uso recomendado |
|---|---|---|
| CSV | `public/CSV.svg` | Badge de tipo de archivo, botón de exportación |
| Excel | `public/Excel.svg` | Badge de tipo de archivo, botón de exportación |
| Extraer | `public/extraer.svg` | Acción de extracción de datos |
| Moon | `public/moon.svg` | Toggle de modo oscuro/claro |

**Regla:** si una ruta no existe, no inventarla. Reemplazar por una regla general o icono inline simple.

---

## REGLAS DE DENSIDAD VISUAL

1. **Foco y escaneo rápido:** el ojo debe saber a dónde ir en <500ms.
2. **Sin sobrecarga cromática:** máximo 2 colores de acento + escala de grises por vista.
3. **Tipografía monoespaciada:** SOLO para valores numéricos (`font-mono` en tablas, KPIs, métricas).
4. **Layouts compactos pero respirables:** padding mínimo de 12px, máximo 24px en contenedores.
5. **Scrollbars limpias:** usar `overflow:auto` con estilizado sutil (`thin` en Firefox, custom scrollbar en Webkit).
6. **Sin bordes o sombras innecesarias:** preferir separación por espaciado y jerarquía tipográfica.

---

## REGLAS POR COMPONENTE

### Canvas

Si existe `components/workspace-canvas.tsx`:
- El canvas debe respirar: padding mínimo 24px alrededor del área de trabajo.
- Fondo sutil tipo dot-grid solo si orienta al usuario sin distraer. Si existe, opacidad máxima 5%.
- Márgenes amplios entre nodos: mínimo 16px.
- Prohibido scroll anidado: si el canvas crece, que crezca el viewport, no un contenedor interno.

### Tablas

Si existe `components/smart-table.tsx`:
- Compactas pero legibles: `text-sm` (14px) como tamaño base, `text-xs` (12px) solo para datos muy densos.
- `font-mono` para toda columna numérica (valores, montos, porcentajes).
- Jerarquía visual por: alineación (texto a izquierda, número a derecha), contraste (headers en `text-muted-foreground`), espaciado (padding horizontal 8px, vertical 4px).
- Scroll horizontal limpio: `overflow-x:auto` con scrollbar estilizado. Nunca truncar columnas.
- Sin rayado zebra: preferir filas con `hover` state sutil y separación por espaciado.
- Columna de acciones: siempre a la derecha, iconos sin texto (tooltip al hover).

### Chat

Si existe `components/chat-interface.tsx`:
- Cuando conviva con tabla o gráfica: chat ocupa máximo **30% del ancho**.
- Si existe patrón de colapso (drawer, panel retráctil), hacerlo colapsable con un toggle visible.
- El área principal de datos debe ser siempre prioritaria (mínimo 70% del ancho).
- Mensajes del sistema: fondo neutro, sin burbujas de colores.
- Input de texto: ancho completo del panel, placeholder descriptivo (`"Pregunta sobre tus datos..."`).

### KPIs y métricas

- Cards de KPI: sin bordes, solo fondo `muted` + sombra sutil o separación por espaciado.
- Valor en `font-mono` + tamaño grande (`text-2xl` o `text-3xl`).
- Label en `text-sm text-muted-foreground`.
- Variación (delta): verde para positivo, rojo para negativo, con icono de flecha.

---

## LOADING Y EMPTY STATES

### Loading states

- **Prohibido** dejar pantallas en blanco durante cargas pesadas.
- Usar **skeletons** que imiten la estructura real del contenido (forma de tabla, forma de card, forma de chart).
- Skeleton debe ser animado con pulse suave (CSS `@keyframes pulse` o `animate-pulse` de Tailwind).
- Si la carga toma >3s, mostrar indicador de progreso o mensaje contextual ("Procesando archivo de 10,000 filas...").
- Para operaciones asíncronas (análisis, exportación), usar barra de progreso indeterminada + CTA para cancelar si aplica.

### Empty states

- **Prohibido** contenedor vacío sin mensaje.
- Todo empty state debe tener:
  1. Icono representativo (sobrio, monocromático)
  2. Mensaje claro de por qué está vacío ("Aún no hay archivos cargados")
  3. CTA principal para resolverlo ("Cargar archivo")
  4. Opcional: enlace secundario de ayuda o documentación
- Sin caricaturas, ilustraciones infantiles o tono demasiado casual.

### Error states

- Mensaje amigable pero informativo: "No pudimos cargar tus datos. Intenta de nuevo."
- Botón de reintento visible.
- Si es error de autenticación o permiso: mensaje específico, no genérico.
- Nunca mostrar stack traces, UUIDs o códigos de error interno al usuario.

---

## CTAs Y PRODUCTIVIDAD

Usar botones específicos y orientados a tarea:

| ✅ Correcto | ❌ Incorrecto |
|---|---|
| "Exportar a Excel" | "Enviar" |
| "Ejecutar Consulta Semántica" | "Continuar" |
| "Descargar Reporte PDF" | "Aceptar" |
| "Subir Archivo" | "Procesar" |
| "Filtrar por Fecha" | "Siguiente" |
| "Aplicar Cambios" | "OK" |

**Reglas:**
- El CTA debe describir la acción real que ocurrirá.
- Sin verbos genéricos: "Enviar", "Continuar", "Procesar" solo si el contexto no deja espacio a ambigüedad.
- Botón primario: 1 por vista. Secundarios: outline o ghost.
- Acciones destructivas: siempre rojo con confirmación ("¿Eliminar archivo?").

---

## ANTIPATRONES A EVITAR

| Anti-patrón | Por qué | Alternativa |
|---|---|---|
| **Dashboards arcoíris** | 6+ colores distintos compiten por atención. | Paleta neutral + 1 acento semántico. |
| **Chat dominante** | Chat ocupa 50%+ de la pantalla en vista analítica. | Máximo 30%, colapsable. |
| **Glassmorphism decorativo** | Fondos borrosos innecesarios que añaden ruido. | Fondos sólidos limpios. |
| **Loaders genéricos** | Spinner circular sin contexto. | Skeleton + mensaje contextual. |
| **Tablas poco legibles** | Rayado zebra, bordes gruesos, números sin mono. | Tabla limpia, mono para números. |
| **Contenedores vacíos** | Espacio en blanco sin mensaje ni CTA. | Empty state completo. |
| **Exceso de bordes o sombras** | Cada card con borde + sombra es ruido visual. | Separación por espaciado + hover state. |
| **Estética SaaS genérica ("AI slop")** | Interfaces que parecen generadas por IA sin criterio humano. | Cada elemento debe tener intención de diseño. |
| **Animaciones decorativas** | Transiciones lentas o rebotes que retrasan al usuario. | Animaciones funcionales, rápidas (<200ms). |
| **Iconos de stock genéricos** | Iconos sobreutilizados sin personalidad. | Assets reales del proyecto o iconos inline simples. |

---

## FORMATO DE RESPUESTA ESPERADO

Cuando este skill se active, responder con:

1. **Diagnóstico visual o UX:** qué problema se detectó y dónde.
2. **Componente afectado:** ruta exacta del archivo.
3. **Criterio de mejora:** qué principio visual o regla aplica.
4. **Propuesta concreta:** cambio específico a implementar.
5. **Impacto esperado:** cómo mejora claridad, productividad o percepción.
6. **Si aplica:** UI/copy sugerido (texto de botones, labels, mensajes).

---

## REFERENCIAS

- `AGENTS.md §15` — Roadmap Comercial (alineación con B2C/B2B)
- `app/globals.css` — Tokens de diseño (colores, tipografía, spacing)
- `tailwind.config.ts` — Configuración de Tailwind (paleta, breakpoints)
- `components/` — Todos los componentes de interfaz
- `public/*.svg` — Assets visuales disponibles
