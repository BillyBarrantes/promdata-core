# PromData Public V1 — Engineering Design

**Estado:** diseño de implementación; no constituye evidencia de despliegue.

## 1. Decisión de lanzamiento

### Beta privada

Una beta privada es aceptable únicamente si se cumplen simultáneamente estas condiciones:

1. Conectores OAuth están deshabilitados **o** fallan cerrados cuando la clave de cifrado no es válida; nunca se persiste un token en texto plano.
2. Todo recurso compartible tiene `team_id` y RLS basada en membresía mediante `EXISTS`; ningún flujo depende de `LIMIT 1` para determinar el equipo.
3. El traductor devuelve un contrato semántico validado y una métrica `missing`, `ambiguous` o `incompatible` bloquea la ejecución en vez de sustituirse.
4. Existe un E2E local real, reproducible y verde para carga, análisis, guardado, reapertura y filtrado autoritativo.
5. Forecast y anomalías permanecen deshabilitados o etiquetados **Experimental** hasta cumplir sus gates estadísticos.

### Lanzamiento público

Además de la beta, requiere: matriz RLS multi-tenant aprobada, cuotas y límites aplicados en servidor, jobs idempotentes y recuperables, trazabilidad por widget/narrativa, observabilidad sin PII, pruebas de carga dentro de las cuotas públicas y regresión visual desktop/móvil.

### Debe permanecer deshabilitado si no cumple el estándar

- OAuth/Google Drive/OneDrive si `OAUTH_TOKEN_ENCRYPTION_KEY` no está presente y validada.
- Forecast, anomalías y alertas prescriptivas si no existen datos/frecuencia/backtest suficientes.
- Cross-filter local cuando el snapshot granular sea truncado, incompleto o de otra versión del dataset.
- Ejecución de una consulta con resolución semántica ambigua o inexistente.

El estado actual explica estas decisiones: `encrypt_token()` devuelve el token original sin Fernet válido ([backend/app/core/oauth_encryption.py](../backend/app/core/oauth_encryption.py#L30-L44)); el callback acepta cualquier host HTTP(S) ([backend/app/services/cloud_oauth.py](../backend/app/services/cloud_oauth.py#L44-L51)); y las políticas heredadas determinan un solo equipo con `LIMIT 1` ([supabase/migrations/0002_setup_rls_policies.sql](../supabase/migrations/0002_setup_rls_policies.sql#L3-L31)).

## 2. Arquitectura objetivo

```text
 Browser / API client
       | JWT + X-Team-Id (selección explícita; no es autoridad)
       v
 FastAPI authentication + TeamContext resolver
       | verifica auth.uid + membership + role; adjunta actor/team/request id
       +-------------------------+-----------------------------------------------+
       |                         |                                               |
       v                         v                                               v
 Upload gateway             Analysis command                              OAuth command
 MIME/signature/size        idempotency key + task row                    allowlisted return path
 streaming                  team/file ownership                            fail-closed encryption
       |                         |                                               |
       v                         v                                               v
 Supabase Storage <---- RLS/Postgres (teams, members, files, tasks, reports, connections)
       |                         |
       v                         v
 profiler + DatasetContract  Celery command consumer
       |                         |
       v                         v
 Versioned dataset        Semantic Translator
 checksum/schema hash       -> Query AnalyticalContract V1
       |                         |  resolved / ambiguous / missing / incompatible
       +-------------------------+-----------------------+
                                                 | validated contract only
                                                 v
                                      Ibis/DuckDB deterministic executor
                                                 |
                        +------------------------+-----------------------+
                        |                                                |
                        v                                                v
                 EvidenceBundle V1                               VisualContract V1
                 facts, query, checksums                         chart/table/KPI, units, filters
                        |                                                |
                        +------------------------+-----------------------+
                                                 v
                                     Narrative renderer / LLM optional
                                     (facts whitelist; deterministic fallback)
                                                 |
                                                 v
               Saved widget/report + evidence pointer + dataset version + layout
                                                 |
                                                 v
    Dashboard: responsive layout, evidence drawer, authoritative interaction contract
                         | local only if complete; otherwise backend recompute
                         v
                task state / audit event / observability redacted
```

### Contratos transversales

| Contrato | Autoridad | Campos obligatorios | Regla |
|---|---|---|---|
| `TeamContext` | backend tras validar JWT | `user_id`, `team_id`, `role`, `request_id` | El frontend solo solicita contexto; no lo autoriza. |
| `DatasetVersion` | ingesta | `file_id`, `team_id`, `version`, `schema_hash`, `content_checksum`, `row_count`, `truncated` | Una ejecución siempre apunta a una versión inmutable. |
| `QueryAnalyticalContractV1` | translator + validator | intención, bindings, filtros, agregaciones, temporalidad, unidad, cobertura, confianza | No llega al executor si tiene errores bloqueantes. |
| `EvidenceBundleV1` | executor | query/plan canónico, facts, warnings, checksum, timestamp | Fuente única para tabla, insight, visual y narrativa. |
| `VisualContractV1` | visual planner | encodings, formato, título, filtros, evidencia, layout priority | Un widget no renderiza datos sin este contrato. |
| `InteractionContractV1` | dashboard/executor | origen de datos, filtros, completitud, recompute policy | Evita que un filtro local parcial parezca autoritativo. |

## 3. Diseños por área

### A. Seguridad multi-tenant y autorización

#### Estado actual relevante

- La RLS heredada de archivos y dashboards usa `get_my_team_id()` con `LIMIT 1`; un usuario con dos equipos no tiene contexto explícito y puede operar en el equipo incorrecto. `profiles` tiene lectura `USING (true)` ([0002_setup_rls_policies.sql](../supabase/migrations/0002_setup_rls_policies.sql#L3-L31)).
- `analysis_tasks`, `saved_reports` y `presentations` todavía están aislados por `user_id`, no por equipo ([20260713000001_create_analysis_tasks.sql](../supabase/migrations/20260713000001_create_analysis_tasks.sql#L5-L35), [20251206_create_saved_reports.sql](../supabase/migrations/20251206_create_saved_reports.sql#L2-L24), [20260404000000_create_presentations.sql](../supabase/migrations/20260404000000_create_presentations.sql#L1-L27)).
- Las conexiones OAuth se persisten por usuario/proveedor, sin `team_id`, y el esquema contiene tokens en columnas de texto ([20260413000000_create_cloud_oauth_core.sql](../supabase/migrations/20260413000000_create_cloud_oauth_core.sql#L1-L55)).

#### Diseño V1 objetivo

**Modelo de datos.** Mantener `users`/`profiles`, `teams` y `team_members`; agregar de forma aditiva `team_id NOT NULL` a todos los recursos de negocio: `uploaded_files`, `analysis_tasks`, `saved_reports`, `presentations`, chats/mensajes, artefactos, `cloud_oauth_connections`, `cloud_watch_targets` y estados de trabajo. `user_id` continúa como actor/propietario de creación; no sustituye el alcance de tenant.

**Contexto.** El cliente puede seleccionar un equipo mediante `X-PromData-Team-Id` o claim de sesión, pero FastAPI crea `TeamContext` solo después de comprobar `EXISTS(team_members where user_id=actor and team_id=requested)`. Cada servicio recibe `TeamContext`, no un `team_id` suelto. El backend además verifica que `file_id`, `presentation_id`, `report_id` y `task_id` pertenezcan a ese equipo antes de leer/escribir.

**Roles ligeros V1.** `admin` puede administrar membresías y recursos del equipo; `member` crea/edita recursos del equipo; `viewer` solo lee. OAuth puede ser `user_owned` en V1, pero su uso debe verificar tanto `connection.user_id == actor` como `connection.team_id == context.team_id`; un modelo de conexión compartida queda fuera de V1.

**RLS patrón.** Para un recurso `r.team_id`, usar una función estable y sin `LIMIT 1` equivalente a:

```sql
EXISTS (
  SELECT 1 FROM public.team_members tm
  WHERE tm.team_id = r.team_id
    AND tm.user_id = auth.uid()
)
```

Las mutaciones usan el mismo predicado en `WITH CHECK`; operaciones administrativas añaden una condición de rol. `profiles` se limita a `auth.uid() = id` salvo campos explícitamente públicos y mínimos. La capa de servicio usa el cliente de usuario cuando sea posible; si usa service role, replica la comprobación de `TeamContext` antes de la consulta.

**OAuth.** El cifrado es una precondición del provider: clave ausente, Fernet inválido o error de cifrado significa `connector_unavailable`, no token plano. El valor persistido debe tener versión/key-id, por ejemplo `enc:v1:<base64>`, para facilitar rotación. `return_to` se almacena como ruta relativa aprobada (`/cargar-datos`) o URL cuyo `origin` pertenezca a la allowlist derivada de configuración; no basta validar esquema y host. Nunca registrar token, verifier, state ni URL completa con parámetros sensibles.

#### Migración compatible

1. Agregar columnas `team_id NULL`, índices `(team_id, id)` y backfill desde el recurso padre o la única membresía existente; registrar filas sin resolución para intervención, no asignar arbitrariamente.
2. Añadir nuevas políticas `EXISTS` sin eliminar todavía las antiguas; en una ventana controlada activar una flag de doble validación en backend y medir denegaciones.
3. Convertir `team_id` a `NOT NULL`, reemplazar políticas heredadas, revocar `profiles USING(true)` y retirar `get_my_team_id()` de rutas nuevas.
4. Incorporar versión de cifrado OAuth; rotar o revocar conexiones existentes sin sobreescribir tokens en claro.

#### Matriz de autorización y aislamiento

| Recurso | Admin | Member | Viewer | Aislamiento obligatorio |
|---|---|---|---|---|
| Archivo/dataset | CRUD | CRUD propio/equipo | lectura | `team_id` + file scope |
| Análisis/tareas | crear, leer, cancelar | crear, leer, cancelar propio | lectura | `team_id`, `file_id`, actor de comando |
| Reporte/presentación | CRUD | CRUD | lectura | `team_id`, evidencia/file referenciado |
| Chat | CRUD | CRUD | lectura si se comparte explícitamente | `team_id`, conversación/file |
| OAuth personal | administrar propio | administrar propio | ninguno | `team_id` + `user_id` |
| Membresías | CRUD | lectura | lectura | rol admin |

#### Tests y aceptación

- Matriz A/B: actor de Team A no puede leer, mutar, adivinar ID, usar `file_id`, tarea, reporte, presentación, chat, storage path ni conexión de Team B.
- Multi-team: mismo usuario elige A y B; cada listado y mutación retorna exactamente el contexto seleccionado.
- RLS: pruebas con JWT de usuario, no solo service role; una query directa a cada tabla protegida debe negar el otro tenant.
- OAuth: clave ausente/inválida rechaza antes de insert; URL externa, host lookalike, esquema no HTTPS y path inesperado son rechazados.

**Cierre binario público:** 100% de pruebas cruzadas verdes y ninguna tabla de recursos en alcance sin `team_id` y política `EXISTS` equivalente.

**Alternativa rechazada:** conservar `LIMIT 1` y filtrar en frontend. Es no determinista para multi-equipo y el cliente no es una frontera de seguridad.

### B. Contrato semántico y exactitud

#### Estado actual relevante

Existe un `AnalyticalContract` V1 para `DatasetContract`, filtros y temporalidad ([backend/app/core/analytical_contract.py](../backend/app/core/analytical_contract.py#L10-L115)); no representa aún resolución de métricas/dimensiones por consulta ni evidencia de bindings. El validator resuelve candidatos y elige el primero (`candidates[0]`) ([validator.py](../backend/app/services/semantic_translator/validator.py#L400-L411)). Más tarde Ibis sustituye una métrica singular inexistente por la primera columna numérica ([ibis_engine.py](../backend/app/services/ibis_engine.py#L887-L905)). Ambos comportamientos son incompatibles con una respuesta pública verificable.

#### Diseño V1 objetivo

Agregar `QueryAnalyticalContractV1` como una **capa aditiva** entre el router/planner y los `AnalysisPlan` existentes. El adaptador convierte solo un contrato `valid` a `AnalysisPlan`; los consumidores actuales permanecen vigentes durante la migración.

```python
class ColumnResolution(BaseModel):
    requested: str
    role: Literal["metric", "dimension", "date", "filter"]
    status: Literal["resolved", "ambiguous", "missing", "incompatible"]
    column: str | None = None
    candidates: list[str] = []
    evidence: dict[str, Any] = {}

class QueryAnalyticalContractV1(BaseModel):
    version: Literal["1.0"]
    query_id: UUID
    dataset: DatasetVersionRef
    intent: Literal["descriptive", "trend", "distribution", "diagnostic", "predictive", "executive"]
    resolutions: list[ColumnResolution]
    aggregations: list[AggregationSpec]
    filters: list[FilterSpec]
    temporal: TemporalSpec | None
    ordering: OrderingSpec | None
    units: list[UnitSpec]
    expected_coverage: CoverageSpec
    confidence: float
    warnings: list[ContractWarning]
    status: Literal["valid", "clarification_required", "blocked"]
```

`ColumnResolution.evidence` incluye coincidencia exacta/normalizada, tipo, rol del profiler, cardinalidad, sample seguro y score; nunca usa una regla de dominio como autoridad. La resolución exacta y única puede pasar. Dos candidatos plausibles producen `ambiguous` y una pregunta de aclaración con los nombres amigables; no se escoge por orden de lista. Una columna ausente produce `missing`; una columna no numérica para `sum`, un identificador como valor, o una fecha no temporal produce `incompatible`.

**Validación.** El validator debe asegurar: existencia física, rol/tipo compatible, agregación permitida, filtros con operador/valor normalizado, granularidad temporal compatible, top-N válido, y unidad declarada. La unidad parte del profiling/metadata y pasa al contrato, `AnalysisPlan`, `VisualContract` y `EvidenceBundle`; la inferencia por nombre queda como señal de baja confianza, no como verdad.

**Regla mínima V1:** eliminar el fallback de métrica en Ibis y devolver un error estructurado `metric_missing` con columnas disponibles. Ibis conserva su Data Shield, pero solo bloquea; no reinterpreta la intención. El planner recibe esa señal y genera aclaración o replanifica con evidencia.

#### Tests adversariales

- métrica inexistente no produce gráfico, KPI ni narrativa;
- dos métricas similares producen `clarification_required`, no `candidates[0]`;
- `sum` sobre texto/ID y `avg` sobre fecha son `incompatible`;
- filtro con columna inexistente o valor incompatible se bloquea, no se descarta silenciosamente;
- dataset snapshot con métrica de stock exige guard y dataset flow no lo aplica;
- unidad de cantidad no genera moneda y una métrica 0–100 no genera `%` sin `UnitSpec.percentage`;
- contrato de versión anterior sigue adaptándose a `AnalysisPlan` durante la transición.

**Alternativa rechazada:** ampliar listas de sinónimos por dominio. No generaliza entre tenants y convierte un problema de incertidumbre en una falsa certeza.

### C. Evidencia determinista y narrativa

#### Estado actual relevante

El pipeline ya preserva parte de la traza de plan/ejecución y el generador de gráficos adjunta `visual_source_payload` y `query_contract` ([chart_generator.py](../backend/app/tasks/analysis_pipeline/chart_generator.py#L215-L263)). La narrativa recibe una serialización truncada de datos (`[:2500]`) y usa instrucciones textuales, pero no existe un verificador posterior de cada cifra ([narrative_generator.py](../backend/app/tasks/analysis_pipeline/narrative_generator.py#L62-L64), [L203-L248](../backend/app/tasks/analysis_pipeline/narrative_generator.py#L203-L248)).

#### Diseño V1 objetivo

Crear `EvidenceBundleV1` por salida ejecutada:

```text
evidence_id, query_id, widget_id, insight_id?
dataset: {file_id, team_id, version, schema_hash, content_checksum}
execution: {engine_version, canonical_query, plan_hash, started_at, completed_at}
scope: {metrics, aggregations, dimensions, filters, period, row_count, completeness}
facts: [{fact_id, kind, value, formatted_value, comparator?, source_rows?, unit}]
artifacts: {table_ref, chart_data_hash, granular_snapshot_ref?}
warnings: [truncation, low_sample, inferred_unit, forecast_experimental]
```

El executor genera facts desde Ibis/DuckDB, no desde el LLM. El `VisualContract` referencia `evidence_id` y `fact_id`; una tabla expone los hechos completos. Para el LLM se entrega una lista compacta y cerrada de facts con IDs, y se solicita salida estructurada `NarrativeClaim { fact_ids, text }`. Un validador acepta cada claim solo si todos sus números, unidad, periodo y comparación se pueden mapear a facts autorizados. Si falla, se muestra una plantilla determinista: titular, hechos y advertencias, sin interpretación adicional.

La UI ofrece un drawer “Ver evidencia” con cálculo, filtros, periodo, versión del archivo y advertencias; el widget muestra solo un icono/tooltip compacto. Exportaciones incluyen `evidence_id`, fecha de generación y estado experimental cuando corresponda.

#### Tests y cierre

- alterar un número del texto narrativo hace fallar la validación;
- porcentaje calculado se compara contra la fórmula/facts con tolerancia definida;
- una narrativa de dataset truncado incluye warning visible;
- fallo del LLM devuelve fallback determinista sin número inventado;
- reabrir un reporte conserva `evidence_id`, checksum y contrato de consulta.

**Cierre binario:** ningún `mensaje_resumen`, alerta o insight público carece de `evidence_id`; 100% de claims numéricos pasan el validador o son eliminados.

### D. Cobertura semántica y dashboard ejecutivo

#### Estado actual relevante

La ejecución canónica limita planes a tres mediante `CANONICAL_SHADOW_TRAFFIC_MIRROR_MAX_PLANS` ([backend/app/core/config.py](../backend/app/core/config.py#L239-L241), [orchestrator.py](../backend/app/tasks/analysis_pipeline/orchestrator.py#L605-L624)). Es un límite técnico; por sí solo no define cobertura ejecutiva. El dashboard ya usa grid responsive, drag/resize y modo presentación ([app/dashboard/page.tsx](../app/dashboard/page.tsx#L1581-L1610)), y el generador construye varios visuales con gobernanza ([chart_generator.py](../backend/app/tasks/analysis_pipeline/chart_generator.py#L40-L83)).

#### Diseño V1 objetivo

Para `intent=executive`, el planner declara un `CoverageSpec`, no incrementa globalmente `max_plans`:

| Bloque | Condición | Salida mínima |
|---|---|---|
| Encabezado | siempre | pregunta, archivo/version, periodo, filtros activos, warnings |
| KPI | >= 1 métrica compatible | 1–3 KPIs, cada uno con unidad y evidencia |
| Tendencia | fecha cardinalidad > 1 | una tendencia para la métrica principal |
| Comparación | dimensión relevante | un ranking/desglose top-N con métrica distinta o perspectiva distinta |
| Diagnóstico | segunda métrica o dimensión válida | variación/comparación, no duplicado de la tendencia |
| Alertas | reglas/facts elegibles | 0..N, cada una evidenciada; ausencia es válida |
| Tabla | siempre que haya resultados | tabla de evidencia o explicación de insuficiencia |

El planificador selecciona por perfil: roles, cardinalidad, completitud, varianza, relación temporal y diversidad de campos. Usa una matriz de redundancia: dos candidatos que comparten métrica, dimensión, periodo, agregación y propósito compiten; gana el de mayor utilidad. Si no hay métrica o dimensión suficiente, el resultado es una vista de perfilado y una solicitud de aclaración, no gráficos decorativos.

`VisualContractV1` contiene `visual_type`, `x/y/series encodings`, `aggregation`, `unit_format`, `currency_code/symbol`, `scale`, `title`, `subtitle`, `active_filters`, `legend_policy`, `empty_state`, `accessibility` (texto alternativo/tabla), `layout_priority`, `evidence_id` y `interaction_contract_id`. El título responde una pregunta analítica; el subtítulo declara periodo y filtros. Leyendas se ocultan solo si una serie es inequívoca; etiquetas no se truncan sin tooltip/tabla alternativa.

**Responsive.** Definir layouts por breakpoint, alturas mínimas por tipo de visual, orden de prioridad y modo de una columna en móvil. La tabla puede desplazarse horizontalmente; un gráfico no debe ocultar unidades/leyenda esencial. Mantener React Grid Layout existente y persistir solo layouts validados.

#### Brecha mínima V1

- añadir `CoverageSpec` y selector declarativo para preguntas amplias;
- bloquear gauge inferido solo por `0 <= value <= 100`: el código actual hace esa inferencia ([plan_executor.py](../backend/app/tasks/analysis_pipeline/plan_executor.py#L202-L214)); usar únicamente `metric_unit=percentage`/ratio explícito;
- incluir header, evidencia y estados insuficientes coherentes en el dashboard;
- someter el dashboard a snapshots visuales con dataset real desktop y móvil.

**Alternativa rechazada:** aumentar `max_plans` a un valor fijo. Aumenta costo y ruido sin asegurar diversidad, evidencia ni orden ejecutivo.

### E. Interacciones y escala correcta

#### Estado actual relevante

Ibis adjunta hasta 100.000 filas granulares para cross-filter y trunca silenciosamente respecto al resultado agregado salvo log ([ibis_engine.py](../backend/app/services/ibis_engine.py#L981-L992)). El frontend puede precargar Arrow para widgets ([app/dashboard/page.tsx](../app/dashboard/page.tsx#L42-L70)).

#### Diseño V1 objetivo

`InteractionContractV1` acompaña cada widget: `dataset_version`, `base_query_hash`, `base_filters`, `snapshot_rows`, `total_rows`, `is_complete`, `transport_mode`, `allowed_filter_columns`, `recompute_policy` y `evidence_id`.

- **Local autoritativo:** solo cuando `is_complete=true`, el snapshot está dentro de cuota y todos los widgets comparten dataset/version/base filters.
- **Local preliminar:** si el snapshot es parcial, puede mostrar un preview claramente etiquetado, pero no modifica KPIs/insights definitivos.
- **Backend obligatorio:** `is_complete=false`, filtro sobre columna ausente, combinación de widgets con universos distintos, snapshot distinto o operación que altere agregaciones. El cliente envía el contrato y el backend genera un nuevo `EvidenceBundle`.
- La UI muestra “Recalculando sobre N filas” y conserva el resultado anterior marcado como desactualizado hasta terminar; errores restauran el estado previo.

#### Límites y cuotas V1

Los límites actuales de filas/columnas/hojas son 250.000/512/12 en la configuración ([backend/app/core/config.py](../backend/app/core/config.py#L177-L190)). Para V1 se deben hacer explícitos y aplicados antes de almacenar/procesar:

| Control | Beta inicial propuesta | Aplicación |
|---|---:|---|
| Tipos | CSV, XLS, XLSX | extensión + MIME + firma/parse seguro |
| Filas analíticas | 250.000 | profiler/ingesta; rechazo, no truncación silenciosa |
| Columnas / hojas | 512 / 12 | ingesta |
| Tamaño de archivo | 50 MiB configurable | streaming gateway + storage policy |
| Snapshot local | 100.000 filas **solo completo** | `InteractionContract` |
| Análisis concurrentes | 1 por usuario, cuota por equipo configurable | Redis + task state |
| Tiempo análisis | soft/hard timeouts configurados | Celery + estado terminal |
| Memoria | límite por worker/contenedor | deployment + métricas |
| Rate limit | por usuario y equipo/ruta | backend; 429 con `Retry-After` |

Los números de cuota deben calibrarse mediante prueba de carga antes de público; son límites de producto, no afirmaciones de Big Data distribuido.

#### Tests y cierre

Dataset de 100.001 filas debe producir `is_complete=false`; la UI no puede aplicar localmente un filtro que cambie una KPI global. Pruebas deben comprobar el envío de contrato, recomputación autorizada, cancelación, restauración tras fallo y no mezcla de versiones.

### F. Forecast, anomalías y alertas honestas

#### Estado actual relevante

El forecast usa frecuencia `M` por defecto, heurística de día de mes y bandas `±5%` crecientes que no provienen del modelo ([backend/app/services/predictive_engine.py](../backend/app/services/predictive_engine.py#L32-L153)). Anomalías usan Isolation Forest con contaminación fija por defecto 0,05 y mínimo cinco valores ([predictive_engine.py](../backend/app/services/predictive_engine.py#L163-L191)).

#### Política V1

Forecast solo es elegible si: fecha validada, métrica numérica compatible, al menos 12 observaciones para no estacional o al menos dos ciclos para estacional, frecuencia detectada de forma determinista, tasa de huecos bajo umbral, snapshot/flow correctamente resuelto y backtest dentro del umbral declarado. La frecuencia se detecta con deltas completos, estacionalidad por candidato y regularidad; si es irregular se pide selección de granularidad o se ofrece tendencia histórica sin forecast.

Los intervalos deben venir de un método que los estime o no mostrarse. Para V1, si el modelo no produce intervalos calibrados, mostrar solo proyección experimental sin banda, junto a error de backtest y horizonte. No llamar “predicción” a un fallback lineal. Forecast se etiqueta `experimental` hasta tener cobertura de series de referencia y métricas de error (MAE, sMAPE/MASE) por tipo de frecuencia.

Una anomalía debe incluir `anomaly_score`, método, contaminación/configuración, población de comparación, periodo y advertencia de que es señal para revisión. No causa recomendación automática de negocio sin una fact determinista; se presenta como “valor atípico detectado”. Series con menos de un umbral de observaciones, demasiados nulos, ID/variable categórica o mezcla de snapshots quedan sin detección.

**Cierre para salir de experimental:** fixture temporal regular/irregular por frecuencia, backtesting reproducible, intervalos calibrados o eliminados, tasa de falsos positivos conocida para anomalías y UI que muestra límites/evidencia.

**Alternativa rechazada:** ocultar las bandas artificiales solo con copy. La corrección es no producir intervalos no estadísticos.

### G. Operación pública, resiliencia y pruebas reales

#### Estado actual relevante

La carga documental lee el archivo completo antes de validar tamaño/tipo ([backend/app/api/routes.py](../backend/app/api/routes.py#L868-L900)). Celery tiene tracking, límites temporales y reintentos de conexión, pero no declara `acks_late` ni `task_reject_on_worker_lost` ([backend/app/celery_app.py](../backend/app/celery_app.py#L41-L95)); por tanto la recuperación de muerte de worker no está garantizada.

#### Diseño V1 objetivo

**Upload.** Streaming hacia un límite de bytes, contador antes de parse, allowlist extensión/MIME/signature, nombre sanitizado, antivirus/escaneo si el proveedor lo habilita, rechazo explícito de ZIP bomb y XLSX corrupto. Guardar metadata de detección y checksum, no el cuerpo en logs.

**Jobs.** `analysis_tasks` evoluciona a una máquina de estados: `queued`, `leased`, `processing`, `retrying`, `completed`, `failed`, `cancelled`, `expired`. Cada comando tiene `idempotency_key` única por equipo/archivo-versión/prompt normalizado/contrato. Configurar `acks_late=True`, `task_reject_on_worker_lost=True` y tareas idempotentes; reservar un lease con expiración. Un reconciliador periódico reencola solamente tareas huérfanas cuyo lease venció, hasta máximo de intentos; persistir `attempt`, `last_heartbeat_at`, `worker_id` y causa terminal. Cancelación es cooperativa entre etapas; no declara completado tras cancelación.

**Observabilidad.** Logs JSON con request/task/team hash, latencia y versión de contrato; nunca prompt completo, tokens, file contents, OAuth state/verifier ni PII. Sentry/Langfuse son opcionales: apagados o con scrubber estricto en local y producción; una caída de observabilidad no bloquea análisis. Healthchecks distinguen API viva, Redis, capacidad de worker, storage y dependencias opcionales.

**Configuración y recuperación.** Variables requeridas se validan al inicio por perfil de entorno; una clave OAuth ausente deshabilita solo conectores. Configuración por entorno es versionada con secretos fuera del repositorio. Se definen backups de Postgres/Storage, retención y procedimiento de rollback de schema aditivo.

#### E2E real local obligatorio

Servicios: Supabase local (Auth, Postgres, Storage), Redis, FastAPI, Celery worker y frontend; Gemini real con clave de desarrollo restringida o un proveedor de test explícitamente marcado. Variables mínimas: URLs/keys de Supabase local, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `NEXT_PUBLIC_API_BASE_URL`, bucket, límite de upload, clave Fernet de prueba y Gemini. Sentry/Langfuse apagados o locales.

Fixture recomendado: `ventas_inventario_v1.xlsx` con hoja `Ventas` (enero-marzo: totales conocidos 300/220/200), dimensión Producto A/B/C y una fila Software excluible; hoja `Stock` con enero 150 y febrero 180 marcado como último snapshot. La prueba verifica:

1. carga real y checksum/version;
2. perfilado identifica fecha, métricas, dimensiones y snapshot;
3. pregunta “ventas mensuales” devuelve 300, 220, 200;
4. pregunta Top por cantidad con filtro excluyente usa exactamente columnas resueltas;
5. stock actual devuelve 180, no suma snapshots;
6. plan, EvidenceBundle y narrativa comparten facts;
7. widget se guarda, reapertura conserva evidencia/version;
8. cross-filter completo es local; fixture sobre el límite fuerza recomputación backend;
9. Team B recibe 403/resultado vacío para todos los IDs de Team A.

**Cierre binario:** el test se ejecuta sin mocks de API, storage, Redis, Celery, executor ni persistencia, y comprueba todos los valores anteriores.

## 4. Backlog de ingeniería

### Hito 0 — Base reproducible y E2E real

**Objetivo:** disponer de un entorno local determinista que pruebe la ruta pública completa.

- Entregables: compose/test harness, fixture XLSX versionada, test E2E real, perfil de variables de test y apagado seguro de observabilidad.
- Dependencias: Supabase local, Redis y worker disponibles para CI/local.
- Pruebas: integración y E2E sin interceptación de backend.
- Cierre: la prueba obligatoria de G pasa tres veces consecutivas y verifica los valores de referencia.
- Riesgo: diferencias entre servicios locales y producción; mitigar con contratos de configuración/health.
- Fuera de V1: despliegue multi-región y Big Data distribuido.

### Hito 1 — Seguridad y aislamiento multi-tenant

**Objetivo:** que identidad, equipo y recurso coincidan en todas las capas.

- Entregables: `TeamContext`, migraciones aditivas de `team_id`, políticas RLS `EXISTS`, OAuth fail-closed/allowlist y rotación de cifrado.
- Dependencias: Hito 0 y estrategia de backfill.
- Pruebas: RLS JWT A/B/multi-team, API service-layer, OAuth security regression.
- Cierre: matriz de autorización completa verde y ninguna tabla en alcance depende de `LIMIT 1`.
- Riesgo: datos heredados sin equipo; se bloquean para migración manual, no se reasignan.
- Fuera de V1: SSO/SAML, SCIM y roles empresariales complejos.

### Hito 2 — Contrato semántico y exactitud

**Objetivo:** ejecutar únicamente intenciones cuya semántica pueda demostrarse.

- Entregables: `QueryAnalyticalContractV1`, adapter compatible a `AnalysisPlan`, estados de resolución, errores/clarificaciones y eliminación del fallback de métrica/gauge por rango.
- Dependencias: DatasetVersion de Hito 0.
- Pruebas: contract/adversarial para métricas, unidades, temporalidad, filtros, snapshots y ambigüedad.
- Cierre: cero sustituciones silenciosas en tests y cada ejecución persiste contrato versionado válido.
- Riesgo: más preguntas terminan en aclaración; es preferible a respuestas falsas.
- Fuera de V1: modelo semántico empresarial editable por admins.

### Hito 3 — Evidencia, narrativa y capacidades predictivas honestas

**Objetivo:** hacer auditables todas las afirmaciones visibles.

- Entregables: EvidenceBundle, validador de claims, fallback determinista, drawer de evidencia, gates de forecast/anomalías.
- Dependencias: Hito 2.
- Pruebas: contradicción narrativa/facts, fallback LLM, backtest, frecuencia irregular, anomalías adversariales.
- Cierre: todo número visible tiene fact/evidence ID y forecast/anomalías se bloquean fuera de precondiciones.
- Riesgo: límite de contexto LLM; se mitiga con facts compactos, no truncamiento silencioso de evidencia.
- Fuera de V1: causalidad automática y recomendaciones autónomas.

### Hito 4 — Dashboard ejecutivo e interacciones autoritativas

**Objetivo:** dashboards concisos, consistentes y correctos al interactuar.

- Entregables: CoverageSpec, VisualContract, header de alcance, tablas de evidencia, estados de insuficiencia, InteractionContract y recompute backend.
- Dependencias: Hitos 2–3.
- Pruebas: diversidad/no redundancia, formato, responsive visual, >100k filas, reapertura/version mismatch.
- Cierre: análisis ejecutivo cumple bloques aplicables o explica por qué no; filtros parciales nunca cambian resultados como si fueran globales.
- Riesgo: costo de recomputación; se mitiga con caché key versionada y feedback de tarea.
- Fuera de V1: colaboración en tiempo real y diseñador libre de dashboards.

### Hito 5 — Operación pública y lanzamiento

**Objetivo:** proteger capacidad, recuperar fallos y publicar solo capacidades verificadas.

- Entregables: streaming uploads, cuotas, state machine/idempotencia/reconciliación, logs redacted, healthchecks, backup/rollback runbooks y gates de release.
- Dependencias: Hitos 0–4.
- Pruebas: límites de upload, rate limit, muerte de worker, retry/cancelación, carga, seguridad y smoke pre-release.
- Cierre: SLOs/limites definidos y pruebas de recuperación verdes; ninguna capacidad experimental se vende como resultado definitivo.
- Riesgo: complejidad operativa; mantener un solo worker tier y cuotas conservadoras en V1.
- Fuera de V1: SLA enterprise, conectores masivos y procesamiento distribuido.

## 5. Matriz de pruebas

| Tipo | Propósito | Ejemplos de gate |
|---|---|---|
| Unit | lógica aislada | resolver columna, formato de unidad, frecuencia, cifrado fail-closed |
| Contract | compatibilidad de payloads | Dataset/Query/Evidence/Visual/Interaction contract versionados |
| Integración | módulos reales | FastAPI + Supabase local; Celery + Redis; Ibis + parquet |
| RLS multi-tenant | aislamiento físico | JWT A/B/multi-team sobre cada tabla/bucket/ruta |
| Adversarial | evitar falsos resultados | métrica missing, ambigüedad, IDs, snapshot histórico, filtros maliciosos |
| E2E real | recorrido usuario completo | fixture XLSX → Gemini → Celery → persistir → reabrir → interactuar |
| Carga/performance | respetar cuotas | archivos límite, concurrencia máxima, timeout y memoria |
| Visual regression | legibilidad y responsive | dashboard ejecutivo desktop/móvil, tabla, warning de filtro parcial |
| Seguridad | frontera pública | OAuth key/redirect, MIME/signature, rate limiting, PII redaction |
| Recuperación/caos | consistencia asíncrona | matar worker, lease vencido, retry idempotente, cancelación, Redis temporalmente caído |

## 6. Alcance comercial resultante

La V1 pública puede prometer: **“PromData transforma archivos CSV, XLS y XLSX de tamaño medio en análisis conversacionales, dashboards interactivos y reportes ejecutivos verificables.”**

No debe prometer Power BI/ThoughtSpot equivalence, Big Data distribuido, PPTX, SVG de entrada, forecast fiable en cualquier archivo ni cross-filter instantáneo sobre datos parciales. Forecast, anomalías y conectores deben figurar como experimental o no estar disponibles hasta pasar los gates de este diseño.
