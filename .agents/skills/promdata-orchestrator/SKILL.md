---
name: promdata-orchestrator
description: Secuenciador y validador determinista de los 17 skills y reglas de PromData.
---

# PROTOCOLO DE VERIFICACIÓN SECUENCIAL

Al recibir una tarea, el agente debe ejecutar esta secuencia de control antes de modificar código:

### Etapa 0: Alineamiento Arquitectónico (Planificación)

Antes de tocar cualquier archivo del Core:
- Generar un artefacto Markdown llamado `implementation_plan.md` en `/tmp/` o la raíz del proyecto.
- Detallar: qué módulos de la Bóveda Intocable se evaden, qué firmas se preservan, cómo el cambio actúa como extensión y no como rewrite.
- Esperar la aprobación explícita del humano antes de escribir la primera línea de código.

### Etapa 1: Validación de Inmutabilidad y Anti-Patterns

- Cargar las reglas de la carpeta `.agents/skills/promdata-fortress-standard` (Fortress Standard).
- Bloquear cualquier cambio en tipos de retorno, estructuras de datos o argumentos en `direction_detector.py`, `_apply_direction_guard_to_distribution_plans`, `_finalize_plans`, y `_detect_literal_filters`.
- Aplicar análisis estático para rechazar los siguientes anti-patterns:
  * Hardcoding de columnas o datos de usuario (ej. `if col_name ==`).
  * Uso de Pandas en hot path (PROHIBIDO >100K filas, usar Ibis + DuckDB).
  * Bloques `except: pass` vacíos.
  * Cero términos de dominio hardcodeados (soluciones estrictamente domain-agnostic).

### Etapa 2: Saneamiento de Frontera (Frontend)

- Cargar los skills `react-best-practices` y `typescript-advanced-types`.
- Verificar que los componentes de la interfaz (como `sanitizeFilterValue` en `chat-interface.tsx`) no alteren o eliminen la serialización de filtros base del backend (`chart_base_filters`). El motor WASM de DuckDB no se modifica.

### Etapa 3: Persistencia y Multi-tenant

- Cargar el skill `supabase-postgres-best-practices`.
- Validar que toda consulta mantenga el aislamiento de datos mediante la inclusión obligatoria de `tenant_id` y `file_id`.

### Etapa 3.5: Cobertura Obligatoria (TDD)

Por cada fix, extensión o nueva funcionalidad implementada:
- El agente TIENE PROHIBIDO pasar a la Etapa 4 sin haber creado o actualizado un test unitario que valide específicamente el nuevo comportamiento.
- El test debe probar el escenario exacto de fallo que el fix resuelve, documentando la intención del cambio.
- **Un bug arreglado sin un test que lo blinde para el futuro es un bug que volverá.**

### Etapa 4: Automatización de Pruebas Reales (Ejecución Física Obligatoria)

- **NO** cargar `playwright-best-practices` ni ningún otro skill. Esta etapa es ESTRICTAMENTE ejecución física en terminal.
- **Prohibido terminantemente** reportar "tests passed" sin haber ejecutado la terminal. Queda prohibido simular resultados exitosos, basarse en ejecuciones anteriores, u omitir la ejecución real.
- Ejecutar FÍSICAMENTE en la terminal de la Mac:
  ```bash
  cd backend && ./run_backend_tests.sh
  ```
- Capturar las últimas 3 líneas LITERALES de la salida de la terminal.
- Si el conteo final es < 32: imprimir últimas 20 líneas con tracebacks y ABORTAR la tarea. No se puede cerrar hasta 32/32.
- Insertar las 3 líneas literales como evidencia en la respuesta al usuario.

### Etapa 5: Cierre Contractual

Imprimir al final de la respuesta el desglose de skills utilizados y el estado binario de la verificación local.
