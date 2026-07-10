---
name: promdata-fortress-standard
description: >
  Protocolo de ingeniería blindado para PromData. Garantiza que cada cambio sea a nivel
  de ingeniería (no parches), protege contra regresiones, y prepara el sistema para
  escalar a múltiples usuarios con datos heterogéneos.
---

# PromData Fortress Standard
**Version:** 3.1 (Titanium Edition)  
**Status:** Active  
**Context:** Production-Grade Multi-Tenant Analytics Platform (Python/FastAPI + Next.js)

## 🔑 PRINCIPIO FUNDAMENTAL: AUTORIDAD DEL DESARROLLADOR

Todas las reglas de este SKILL protegen contra cambios **accidentales del agente**.
El desarrollador humano tiene **autoridad absoluta** para ordenar cualquier cambio,
incluyendo modificar, reemplazar o eliminar código protegido, cuando lo solicita explícitamente.

**Jerarquía de Autoridad:**
1. **Orden explícita del usuario** → Se ejecuta (el SKILL se flexibiliza)
2. **Reglas del SKILL** → El agente las sigue cuando no hay orden contraria
3. **Criterio del agente** → Se subordina siempre a los dos anteriores

---

## 🚨 PROTOCOLO DE ACTIVACIÓN

Este Skill se activa **automáticamente** cuando el agente necesita modificar:
- Backend: `/backend/app/services/`, `/backend/app/core/`, `/backend/app/api/`
- Frontend: `/components/`, `/app/`, `/lib/`, `/hooks/`
- Infraestructura: `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `package.json`
- Cualquier archivo que contenga lógica de negocio o procesamiento de datos

---

## ⚡ MODO OVERRIDE (Solo bajo orden explícita)

Si el usuario inicia su solicitud con **`[OVERRIDE]`**:
1. Suspender las validaciones de este Skill.
2. Ejecutar la orden literalmente.
3. Marcar todo código generado con `# TODO: REFACTOR (Technical Debt from Override)`.
4. Advertir al usuario qué reglas se están violando.

**Sin `[OVERRIDE]`, TODAS las reglas son obligatorias sin excepción.**

---

## 🏛️ MÓDULO 1: THE FORTRESS (Protección Anti-Regresión Absoluta)

*Objetivo: Ningún cambio puede destruir, degradar ni alterar funcionalidad existente sin orden explícita.*

### 1.1 Ley de Inmutabilidad del Core

Antes de modificar **cualquier función existente**:

1. **Auditoría de Impacto:**
   - Buscar en TODO el proyecto quién invoca esta función (grep de nombre + alias).
   - Identificar los contratos de entrada (argumentos) y salida (return type/shape).
   
2. **Prohibición de Cambio Destructivo:**
   - **PROHIBIDO** cambiar el `return type`, la estructura del return, o los argumentos obligatorios.
   - **PROHIBIDO** eliminar parámetros existentes.
   - Si se necesita nueva funcionalidad: agregar **argumentos opcionales** con valores por defecto.
   - Si la firma debe cambiar: crear una **función nueva** y marcar la anterior con `# @deprecated`.

3. **Protocolo de Deprecación:**
   - Las funciones obsoletas se marcan con `# @deprecated("Usar nueva_funcion()")`.
   - **NUNCA** borrar código — solo el usuario humano puede eliminar código.

### 1.2 Ley de No-Eliminación (Zero Deletion Policy)

- **PROHIBIDO** eliminar archivos, clases, funciones, imports o bloques de lógica existentes.
- **PROHIBIDO** reemplazar implementaciones completas (rewrite) sin orden explícita del usuario.
- **PROHIBIDO** hacer refactoring masivo que mueva lógica entre archivos sin aprobación.
- Si algo "ya no sirve", agregar el comentario `# DEPRECATED` pero **no borrar**.

### 1.3 Ley de Preservación de Guards y Shields

Los mecanismos de protección existentes son **INTOCABLES** salvo orden explícita:
- `ID Shield` (Anti-Float para identificadores)
- `Text Guard` (Preservación de texto vs números)
- `Mixed Content Guard` (Detección de contenido mixto)
- `Date Guard` (Protección contra falsos positivos temporales)
- `Entropy Sanitization` (Degradación de columnas de baja entropía)
- `Snapshot Logic` (Prevención de sumas ciegas en inventarios)

**Regla:** Si el agente necesita modificar un Guard/Shield, debe:
1. Explicar por qué el guard actual falla.
2. Mostrar un caso de prueba donde falla.
3. Proponer la mejora **como extensión**, no como reemplazo.

### 1.4 Ley de Verificación Pre-Commit

Antes de presentar cambios como "listos":
1. Verificar que los imports existentes siguen funcionando.
2. Verificar que las funciones modificadas mantienen compatibilidad de firma.
3. Verificar que no se eliminó ningún bloque de código.

### 1.5 Ley de Import Blindness (Import faltante)

- **PROHIBIDO** usar un símbolo en el cuerpo de una función sin que esté importado explícitamente en el bloque de imports del archivo.
- Por cada nuevo símbolo importado en el cuerpo (ej: `emit_structured_log`), verificar que el `import` esté presente en la cabecera del archivo.
- **PROHIBIDO** usar `from X import *` salvo en `__init__.py` de paquetes.

**Check automático post-cambio:** `grep -n "import " <archivo_modificado>` + revisar que todo símbolo usado en el cuerpo tenga su import correspondiente.

---

## 🏗️ MÓDULO 2: THE ENGINEER (Estándar de Ingeniería, No Parches)

*Objetivo: Cada cambio debe ser una solución de ingeniería escalable, no un hotfix temporal.*

### 2.1 Ley Anti-Parche (Engineering-First)

Todo cambio debe cumplir **TODOS** estos criterios:

| Criterio | Pregunta de Validación | Ejemplo Prohibido |
|----------|----------------------|-------------------|
| **Generalizable** | ¿Funciona para CUALQUIER archivo de CUALQUIER usuario? | `if col == 'ventas': ...` |
| **Data-Driven** | ¿La decisión se basa en DATA, no en nombres? | `if 'stock' in col_name: ...` |
| **Escalable** | ¿Funciona con 10 filas Y con 10 millones? | Usar `.apply()` con lambda en loops |
| **Resiliente** | ¿Qué pasa si la data viene vacía/corrupta? | No manejar `df.empty` o `NaN` |
| **Documentado** | ¿El código explica el POR QUÉ, no solo el QUÉ? | Función sin docstring ni comentarios |

**Si un cambio no cumple los 5 criterios, es un parche y debe ser rechazado.**

### 2.1.1 Cláusula de Repotenciación Controlada

Se **PERMITE** modificar código estable y actualmente funcional **solo si** la repotenciación cumple todas estas condiciones:

1. **Objetivo estructural:** La mejora resuelve un problema real de latencia, escalabilidad, resiliencia, multi-tenant, observabilidad o mantenibilidad del core.
2. **Contrato intacto:** Se preservan firmas, payloads, retornos, side-effects esperados y compatibilidad con los consumidores actuales.
3. **Extensión, no reemplazo:** La mejora se implementa como extensión, fast-path, guard adicional, caché, router, feature flag o fallback compatible. **No** como rewrite total.
4. **Evidencia previa:** Debe existir señal objetiva del cuello o riesgo: logs, métricas, trazas, profiling, errores repetibles o evidencia funcional concreta.
5. **Alcance mínimo:** Se toca el módulo correcto y la menor superficie posible. Está **prohibido** modificar rutas sanas no relacionadas solo por simetría o limpieza cosmética.
6. **Validación proporcional:** Debe verificarse como mínimo compilación/imports, smoke test, compatibilidad del flujo anterior y evidencia de que no hubo regresión funcional.

**Regla operativa:** Si una optimización macro exige tocar una pieza sana del sistema para evitar duplicación, eliminar rutas lentas o preservar consistencia arquitectónica, el agente **debe hacerlo** bajo esta cláusula.

**Prohibiciones explícitas bajo esta cláusula:**
- No usarla para justificar refactors amplios sin evidencia.
- No usarla para sustituir Guards/Shields existentes en vez de extenderlos.
- No usarla para cambiar comportamiento de runtime no relacionado con el cuello diagnosticado.
- No usarla para introducir deuda técnica silenciosa o atajos hardcodeados.

**Estándar de decisión:** Entre:
- duplicar lógica para “no tocar lo que funciona”, o
- mejorar el módulo correcto manteniendo compatibilidad,

el agente debe elegir la **segunda opción**.

### 2.1.2 Ley Anti-Shadowing (Flujos Muertos)

Está **ESTRICTAMENTE PROHIBIDO** introducir condicionales (`if`/`return`) en la parte superior de una función (early returns) que provoquen que la lógica base del sistema (Guardias, Shields o Validaciones) quede aislada y nunca se ejecute.

**Reglas operativas:**
1. Todo nuevo fast-path debe **ceder el control al flujo original** si no es aplicable — nunca hacer un "bypass" ciego.
2. Si un fast-path retorna temprano, debe hacerlo SOLO cuando las condiciones del fast-path se cumplan exactamente. Si no, debe delegar al flujo original.
3. Está permitido agregar guards al inicio que validen precondiciones (ej: `if df.empty: return []`), pero no para desviar lógica de negocios hacia un camino alternativo no autorizado.

**Ejemplo prohibido:**
```python
def analyze(intent):
    if intent.type == "trend" and intent.split_dimension:
        return _fast_trend_path(intent)  # ❌ Bypassa todo el engine legacy
```

**Ejemplo permitido:**
```python
def analyze(intent):
    if intent.type == "trend" and intent.split_dimension:
        if _fast_trend_applies(intent):
            return _fast_trend_path(intent)  # ✅ Fast-path condicional
    # Si no aplica, cae al flujo original
    return _legacy_engine(intent)
```

### 2.2 Ley del Código Hardcodeado (Zero Hardcode)

- **PROHIBIDO** hardcodear nombres de columnas, valores, IDs de tenant, o rutas.
- **PROHIBIDO** usar `if col_name == 'precio': ...` — siempre usar detección por comportamiento de datos (cardinality, dtype, statistical properties).
- **PROHIBIDO** hardcodear credenciales, URLs o configuraciones en el código fuente.
- Toda configuración variable va en `.env` o en constantes con nombre descriptivo.

### 2.3 Ley de Tipado Estricto

Todo código nuevo **DEBE** incluir Type Hints:
```python
# ✅ CORRECTO
def calcular_kpi(data: pd.DataFrame, target: str) -> float: ...
def classify_column(series: pd.Series) -> dict[str, str]: ...

# ❌ PROHIBIDO
def calcular_kpi(data, target): ...
```

### 2.4 Ley de Lazy Evaluation (Rendimiento)

- Si la operación toca >100,000 filas, Pandas en el hilo principal está **PROHIBIDO**.
- Preferir Ibis/DuckDB (Lazy) para transformaciones pesadas.
- Evitar `.iterrows()`, `.apply()` con lambda, y loops sobre DataFrames.
- Preferir operaciones vectorizadas.

### 2.5 Ley de Manejo de Errores

- **PROHIBIDO** usar `except: pass` o `except Exception: continue` sin logging.
- Todo error debe ser capturado con contexto: qué columna, qué operación, qué tipo de dato.
- Los errores deben ser graceful: nunca crashear, siempre retornar un estado seguro.

---

## 📊 MÓDULO 3: THE STRATEGIST (Visual & Analítico)

*Objetivo: Gráficos empresariales profesionales y análisis correcto.*

### 3.1 Matriz de Decisión Visual

El agente debe sugerir la alternativa profesional:
- **Evolución Temporal** → Gráfico de Líneas o Área (**nunca** barras o pie para tiempo).
- **Comparación de Magnitudes** → Barras Horizontales.
- **Composición / Partes de un Todo** → Donut (máximo 6 categorías, agrupar el resto en "Otros").
- **Flujo Financiero** → Cascada (Waterfall).
- **Correlación** → Scatter Plot.

### 3.2 Reglas de KPI

- Todo KPI numérico debe poder responder "¿por qué?" (drill-down).
- Los porcentajes se muestran como `%`, las monedas con su símbolo detectado.
- Los números grandes usan formato legible: `1,250,000` no `1250000`.

### 3.3 Ley del ID Shield Visual

- En gráficos: los IDs y códigos son **ejes/etiquetas**, nunca valores a graficar.
- Si una columna tipo `dimension` aparece en un eje de valores, el agente debe rechazarlo.

---

## 🔒 MÓDULO 4: THE SENTINEL (Integridad de Datos Multi-Tenant)

*Objetivo: Preparar para múltiples usuarios con datos completamente diferentes.*

### 4.1 Ley de Schema-Agnostic Absoluto

- El sistema **NUNCA** puede asumir estructura de datos.
- Toda decisión debe basarse en: `dtype`, `cardinality`, `cardinality_ratio`, propiedades estadísticas.
- Los nombres de columnas son **decorativos** — la clasificación se hace por CONTENIDO.

### 4.2 Ley de Aislamiento de Tenant

- Los datos de un usuario NUNCA pueden filtrarse a otro.
- Cada operación debe validar `tenant_id` / `report_id`.
- Las rutas de archivos temporales deben incluir el `file_id` como namespace.

### 4.3 Ley de Snapshot vs Flow

- Antes de agregar (`SUM`) una columna métrica a través del tiempo, verificar si es `SNAPSHOT` o `FLOW`.
- Si es SNAPSHOT (inventario, balance, stock): filtrar por `MAX(date)` antes de sumar.
- Si es FLOW (ventas, gastos, transacciones): se puede sumar libremente.

---

## 📋 MÓDULO 5: THE PROTOCOL (Flujo de Trabajo Obligatorio)

*Objetivo: Proceso estándar que el agente debe seguir en cada tarea.*

### 5.1 Checklist Pre-Cambio (Obligatorio)

Antes de escribir cualquier línea de código, el agente debe:

1. [ ] **Leer** los archivos afectados completos (no solo la zona del cambio).
2. [ ] **Identificar** todas las funciones/componentes que serán impactados.
3. [ ] **Verificar** que el cambio es Schema-Agnostic (sin hardcodes).
4. [ ] **Confirmar** que no viola ninguna ley de los módulos anteriores.

### 5.2 Checklist Post-Cambio (Obligatorio)

Después de cada cambio, el agente debe verificar:

1. [ ] No se eliminó código existente (salvo override explícito).
2. [ ] Los Guards/Shields siguen intactos.
3. [ ] Las firmas de funciones existentes no cambiaron.
4. [ ] El código nuevo tiene Type Hints y docstrings.
5. [ ] No hay valores hardcodeados.
6. [ ] **Git Diff Audit:** Ejecutar `git diff --stat` + `git diff <archivos_tocados>` para demostrar matemáticamente que NO se eliminaron líneas de lógica existente (solo se permiten líneas borradas si son whitespace, reemplazos exactos de firmas con wrappers, o bajo `[OVERRIDE]`).
7. [ ] **Import Blindness Check:** Revisar que todo nuevo símbolo usado en el cuerpo tenga su import correspondiente en la cabecera del archivo.
8. [ ] **Pydantic Field Drift Check:** Por cada `getattr(modelo, "campo", ...)`, verificar que `"campo"` exista como field declarado en el modelo.

### 5.3 Protocolo de Comunicación

- El agente debe informar **qué cambiará y por qué** antes de hacerlo.
- Si un cambio toca más de 2 archivos, solicitar confirmación del usuario.
- Siempre mostrar el diff o resumen de cambios al finalizar.

---

## 🧬 MÓDULO 6: THE EVOLVER (Evolución Controlada)

*Objetivo: El código crece de manera orgánica y versionada.*

### 6.1 Ley de Extensión sobre Modificación

- Preferir **agregar** funciones nuevas sobre **modificar** las existentes.
- Preferir **wrappers** sobre cambios directos a firmas.
- Preferir **argumentos opcionales** sobre argumentos nuevos obligatorios.

### 6.2 Ley de Versionamiento Semántico del Código

- Cuando se agrega funcionalidad nueva, actualizar el docstring con versión:
  ```python
  """Motor de Datos V7.1 — Agregado soporte para archivos JSON."""
  ```
- Los comments de versión (`V3`, `V7`, etc.) ya existentes son parte de la historia y **NO se borran**.

### 6.3 Ley de Coexistencia

- Si se crea un nuevo motor/approach, el anterior debe seguir funcionando.
- Ejemplo: Si se crea `ibis_engine_v2.py`, `ibis_engine.py` sigue activo hasta migración explícita.

### 6.4 Ley de Pydantic Field Drift (Campos Fantasma)

Cuando un dict contiene una key que el código espera leer via `getattr(intent, key, [])`:
1. Verificar que el field exista como atributo declarado en el modelo Pydantic correspondiente.
2. Si el field **no existe**, Pydantic v2 (con `extra="ignore"` por defecto) lo descarta silenciosamente y `getattr` retorna siempre el default.
3. **Obligatorio:** declarar el field explícitamente en el modelo con `Field(default_factory=list)` o el tipo adecuado.
4. Excepción: si el modelo usa `model_config = {"extra": "allow"}`, los campos extra se preservan pero deben ser accedidos via `model.extra` o validación explícita.

**Check automático:** por cada `getattr(modelo, "campo_no_estandar", ...)`, verificar que `"campo_no_estandar" in type(modelo).model_fields` retorne True.
