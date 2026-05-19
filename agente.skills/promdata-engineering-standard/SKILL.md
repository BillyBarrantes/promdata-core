---
name: promdata-engineering-standard
description: Standards and protocols for PromData development, focusing on data integrity, scalability, and visual consistency.
---

# PromData Engineering Standard
**Version:** 2.0 (Titanium Guard)
**Status:** Active
**Context:** Production-Grade Python/Next.js Architecture

## 🚨 PROTOCOLO DE ACTIVACIÓN
Este Skill se activa automáticamente cuando el usuario solicita cambios en:
- Lógica de Backend (`/app/services`, `/app/core`)
- Componentes de Visualización (`/components/charts`)
- Migración de Datos (Pandas -> Ibis)

---

## ⚡ MODO DIOS (Emergency Override)
Si el usuario inicia su solicitud con **`[OVERRIDE_PROTOCOL]`**:
1.  **Suspender** todas las validaciones de este Skill.
2.  Ejecutar la orden literalmente (útil para Prototipado Rápido o Hotfixes).
3.  **Advertencia:** El agente debe agregar el comentario `# TODO: REFACTOR (Technical Debt)` en el código generado.

---

## 🏛️ MÓDULO 1: THE GUARDIAN (Integridad & Anti-Regresión)
*Objetivo: Proteger el Core de PromData de "Mejoras Destructivas".*

### 1.1 Ley de Inmutabilidad Funcional
Antes de modificar una función existente en `ibis_engine.py` o `data_engine.py`:
1.  **Check de Referencias:** Busca en todo el proyecto quién usa esta función.
2.  **Si hay dependencias:** NO cambies el `return type` ni los argumentos obligatorios. Crea un `wrapper` o un nuevo argumento opcional.
3.  **Deprecation Protocol:** Si una función ya no sirve, **NO LA BORRES**. Márcala con el decorador `@deprecated("Usar nueva_funcion() en su lugar")`. Solo el usuario humano puede borrar código.

### 1.2 Ley del "ID Shield" (Anti-Float)
Los identificadores (SKU, DNI, RUC, Codigo Almacén) son **Etiquetas**, NO números.
- **Prohibido:** Permitir inferencia de tipos que convierta "0130" en `130.0`.
- **Obligatorio:** Forzar `dtype=str` o casting explícito `str().zfill()`.

### 1.3 Ley del "Snapshot Isolation" (Anti-Suma Ciega)
En logística, el stock es una foto (Snapshot), no un flujo.
- **Regla:** Nunca sumar columnas de `stock` o `balance` a través del tiempo.
- **Validación:** Si detectas una agregación (`SUM`) sobre inventario, verifica que exista un filtro `date == MAX(date)`.

---

## 🏗️ MÓDULO 2: THE SCALABILITY ARCHITECT (Big Data 12 Pillars)
*Objetivo: Preparar el sistema para Gigabytes de datos.*

### 2.1 Lazy Evaluation First
- **Preferencia:** Utilizar expresiones de Ibis/DuckDB (Lazy) sobre DataFrames de Pandas (Eager) para operaciones de transformación pesada.
- **Límite:** Si la operación implica >100,000 filas, Pandas está prohibido en el hilo principal.

### 2.2 Analítica Diagnóstica (El "Por Qué")
- **Regla:** Todo KPI numérico debe ir acompañado de un desglose.
- **Ejemplo:** Si calculas "Ventas Totales", el código debe estar preparado para responder "¿Qué región bajó más?" (Drill-down).

---

## 📊 MÓDULO 3: THE VISUAL STRATEGIST (El Arquitecto de la Información)
*Objetivo: La visualización debe servir a la intención del dato, no a un dogma estético.*

### 3.1 Matriz de Decisión Gráfica (NUEVO ESTÁNDAR)
- **Evolución Temporal:** → SIEMPRE Línea (Tendencias).
- **Comparación de Magnitudes:** → Preferir Barras (Ranking).
- **Composición / Parte-Todo:**
    - **Treemap:** Para jerarquías complejas o > 5 categorías.
    - **Pie Chart (Donut):** PERMITIDO SOLO SI:
        - El usuario lo solicita explícitamente.
        - O hay menos de 5 categorías principales. Nota: Si hay más datos, usar agrupación "Otros".
- **Flujo Financiero / P&L:** → OBLIGATORIO Waterfall (Ingresos vs Egresos).
- **Correlación / Densidad:** → Usar Scatter Plot o Heatmap.

### 3.2 Regla de Soberanía del Usuario
Si el usuario solicita un tipo de gráfico específico (ej: "Quiero un Pie Chart"), **OBEDECE inmediatamente**, ignorando las preferencias del sistema. Tu rol es aconsejar, no bloquear.

---

## ⚡ MÓDULO 4: THE PERFORMANCE ENGINEER (Python Best Practices)
*Objetivo: Código Limpio y Profesional.*

### 4.1 Tipado Estricto (Type Hinting)
Todo código nuevo debe incluir Type Hints para entradas y salidas.
```python
# Correcto
def calcular_kpi(data: pd.DataFrame, target: str) -> float: ...
```

## 🛡️ MÓDULO 6: ZERO COLLATERAL DAMAGE (Edición Quirúrgica y Anti-Amnesia)
*Objetivo: Proteger el código funcional existente de alteraciones no solicitadas o reescrituras perezosas.*

### 6.1 Principio de Aislamiento Estricto
- **Regla de Oro:** Si el usuario solicita modificar o agregar la "Funcionalidad A" en un archivo, está **ESTRICTAMENTE PROHIBIDO** alterar, refactorizar, mover o borrar la "Funcionalidad B" que vive en ese mismo archivo, a menos que se solicite explícitamente.
- **Acción:** Antes de guardar un cambio, haz un auto-check: *"¿Este cambio rompe o altera la lógica de los imports, estados o componentes hermanos?"* Si la respuesta es sí, aborta y replantea usando React Portals o abstracciones.

### 6.2 Prohibición de "Lazy Coding" (Código Perezoso)
- Los LLMs tienden a omitir código funcional existente para ahorrar tokens al mostrar un archivo (ej. usar comentarios como `// ... resto del código anterior ...`).
- **Regla:** Al modificar un componente complejo (como `app/dashboard/page.tsx`), debes asegurarte de que el código generado mantenga **intacta** toda la lógica previa de importaciones, hooks, contextos y providers. Nunca asumas que el usuario unirá los fragmentos correctamente si tú omites las partes vitales.

### 6.3 Las "Bóvedas Intocables" (Core Inviolable)
Cualquier optimización UI/UX debe construirse **alrededor** de estos motores, nunca a través de ellos. Bajo ninguna circunstancia puedes alterar la mecánica de:
1.  **Motor DuckDB-Wasm:** (Inicialización, Workers, persistencia en memoria).
2.  **Transporte Apache Arrow:** (Serialización IPC y deserialización en cliente).
3.  **React Grid Layout:** (Mecánicas de Drag & Drop, ResizeObserver, persistencia de coordenadas `x,y,w,h`).
Si una nueva mejora interfiere con las Bóvedas Intocables, debes detenerte, advertir al usuario, y proponer una solución arquitectónica que las evada (ej. `createPortal`, Z-index absolutos, contextos separados).

---

## 🏗️ MÓDULO 7: THE ENTERPRISE ARCHITECT (Cero Parches y Deuda Técnica)
*Objetivo: Construir software que dure 10 años, no 10 días.*

### 7.1 Prohibición de "Hack/Band-Aid Coding"
- Está **ESTRICTAMENTE PROHIBIDO** usar parches temporales para resolver problemas arquitectónicos.
- **Evitar a toda costa:** 
  - `!important` en CSS para forzar estilos.
  - `setTimeout` mágicos para esperar que algo renderice (usar useEffect o callbacks reales).
  - Anidamiento excesivo de `divs` solo para alinear algo.
  - `any` en TypeScript (usar interfaces estrictas).
- **Acción:** Si la solución requiere un "parche feo", detente. Explícale al usuario que hay un error de arquitectura subyacente y propón la refactorización profunda (Ej: usar React Portals en lugar de pelear con el z-index).

### 7.2 Solución de Causa Raíz (Root Cause Analysis)
- Cuando algo falle (ej. un gráfico se deforma o el Grid salta), no ataques el síntoma (ej. forzar un ancho fijo). Ataca la **enfermedad** (ej. investigar el Stacking Context o el flujo flexbox del ancestro).

---

## 🛡️ MÓDULO 8: THE DEFENSIVE PROGRAMMER (Resiliencia y Manejo de Errores)
*Objetivo: El software Enterprise no se "crashea", se degrada con gracia.*

### 8.1 Fin de la programación "Happy Path"
- Los LLMs asumen que los datos siempre llegan perfectos y la red siempre es rápida. En la vida real, las APIs fallan y los datos vienen nulos.
- **Regla:** Todo fetch a Supabase, toda llamada a FastAPI y toda consulta a DuckDB-WASM **DEBE** estar envuelta en bloques `try/catch`.
- **UI/UX Obligatoria:** Siempre debes proveer 3 estados en tus componentes React:
  1. `Loading` (Skeleton loaders, no simples textos de "cargando...").
  2. `Error` (Mensajes amigables y botón de reintento, nunca una pantalla en blanco).
  3. `Success` (El componente renderizado).

### 8.2 Fallbacks de Datos Seguros
- Nunca accedas a propiedades de objetos anidados sin seguridad. En lugar de `data.user.profile.name`, usa Optional Chaining (`data?.user?.profile?.name ?? 'Usuario Desconocido'`).

---

## 🧩 MÓDULO 9: THE CLEAN CODER (Modularidad y Contención)
*Objetivo: Evitar archivos monolíticos y espagueti.*

### 9.1 Regla del Archivo Liviano
- Si un componente (ej. `app/dashboard/page.tsx`) está creciendo descontroladamente porque le agregas nuevas funciones (como paneles laterales, modales o gestores de estado complejos), **TIENES PROHIBIDO** seguir inyectando código allí.
- **Acción:** Debes extraer la nueva funcionalidad en un componente independiente dentro de la carpeta `/components` y simplemente importarlo en la página principal. Mantén las páginas limpias como orquestadores, no como basureros de lógica.