import ibis
import pandas as pd
import datetime
import re
from app.services.data_engine import DataEngine
from app.services.snapshot_guard import should_apply_latest_snapshot_filter
from app.services.semantic_translator.temporal_resolver import (
    normalize_intent_temporal_filters,
    _extract_year_from_any_string,
)
from app.core.semantic_grammar import (
    AnalysisPlan, DescriptiveIntent, TimeTrendIntent, DistributionIntent, 
    TimeGrain, DiagnosticIntent, PredictiveIntent, DataFilter
)

class IbisEngine:
    """
    Motor de ejecución analítica basado en Ibis + DuckDB.
    Lee archivos Parquet (Fase 1.1) y ejecuta planes semánticos (Fase 1.2).
    """

    @staticmethod
    def _resolve_frame_key(frame_id: str, available_keys: list[str]) -> str | None:
        """Resuelve frame_id corto ('sheet::2022') a clave completa con prefijo.

        El LLM emite IDs en formato corto ('sheet::2022') pero las claves en
        related_tables tienen prefijos ('primary__', 'related__'). Esta función
        busca por sufijo para resolver el mismatch.
        """
        if frame_id in available_keys:
            return frame_id
        suffix = f"__{frame_id}"
        for key in available_keys:
            if key == suffix or key.endswith(suffix):
                return key
        return None

    # ═══════════════════════════════════════════════════════════════════
    # IBIS_FUNC_MAP — Catálogo de funciones de agregación soportadas
    # ═══════════════════════════════════════════════════════════════════
    # Mapea funciones del lenguaje natural (generadas por el traductor
    # semántico) a métodos reales del motor Ibis.
    #
    # Soporte: MAX, MIN, SUM, AVG/MEAN, MEDIAN, COUNT, COUNT_DISTINCT,
    #          STDDEV/STD, VARIANCE/VAR
    #
    # Usado en _build_filter_expression para resolver filtros que
    # comparan contra valores agregados (ej. salario < MEDIAN(salario)).
    # ═══════════════════════════════════════════════════════════════════
    IBIS_FUNC_MAP: dict[str, str] = {
        'max': 'max',
        'min': 'min',
        'sum': 'sum',
        'avg': 'mean',
        'mean': 'mean',
        'median': 'median',
        'count': 'count',
        'count_distinct': 'nunique',
        'stddev': 'std',
        'std': 'std',
        'variance': 'var',
        'var': 'var',
    }

    @staticmethod
    def _round_result(obj, decimals=2):
        """
        🧹 [FASE 1] Sanitizador Global de Precisión.
        Recorre recursivamente cualquier dict/list y redondea floats a N decimales.
        Elimina los números con 8+ decimales en TODA la salida de IbisEngine.
        """
        if isinstance(obj, dict):
            return {k: IbisEngine._round_result(v, decimals) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [IbisEngine._round_result(item, decimals) for item in obj]
        elif isinstance(obj, float):
            return round(obj, decimals)
        return obj

    @staticmethod
    def _auto_cast_columns(t, protected_cols: list = []):
        """
        Universal Column Auto-Caster (Schema-Agnostic V7).
        Inspects ALL columns in the Ibis table. If a column is String but 
        contains numeric data, casts it to float64.
        
        🛡️ [FASE 4D] IMMUTABILITY LOCK:
        Skips any column present in 'protected_cols' (dimensions/ids detected by DataEngine).
        """
        # Normalize protected columns for case-insensitive matching
        protected_lower = [c.lower() for c in protected_cols]
        
        for col_name in t.columns:
            # 🛡️ [GUARD] If the column is protected, DO NOT TOUCH IT.
            if col_name.lower() in protected_lower:
                print(f"   🛡️ [IMMUTABILITY LOCK] Skipping Auto-Cast for protected dimension: '{col_name}'")
                continue

            col = t[col_name]
            col_type = str(col.type()).lower()
            
            if 'string' in col_type or 'utf8' in col_type or 'varchar' in col_type:
                # Test if this string column can be cast to numeric
                try:
                    # Try casting a sample to verify
                    _ = t.select(col_name).limit(5).mutate(
                        _test=t[col_name].cast('float64')
                    ).execute()
                    
                    # If no error, cast the whole column
                    t = t.mutate(**{col_name: t[col_name].cast('float64')})
                    print(f"   📊 [AUTO-CAST] '{col_name}': String → Float64")
                except Exception:
                    pass  # Not numeric, keep as string
        
        return t

    @staticmethod
    def _format_chart_name(col_name, val):
        """
        🗣️ [FASE 4D] Context Injection (Anti-Hallucination).
        If the value is a naked number (e.g. "810"), prepend the column name.
        Example: "810" -> "Tipo Almacen 810"
        """
        # 📅 Normaliza fechas para evitar ruido visual "00:00:00" cuando el dato real es diario.
        if isinstance(val, (pd.Timestamp, datetime.datetime, datetime.date)):
            ts = pd.Timestamp(val)
            if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
                return ts.strftime("%Y-%m-%d")
            return ts.strftime("%Y-%m-%d %H:%M")

        if isinstance(val, str):
            text = val.strip()
            if len(text) >= 10 and text[:4].isdigit() and text[4] in "-/" and text[7] in "-/":
                parsed = pd.to_datetime(text, errors='coerce')
                if pd.notna(parsed):
                    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
                        return parsed.strftime("%Y-%m-%d")
                    return parsed.strftime("%Y-%m-%d %H:%M")

        val_str = str(val).strip()
        # Check if it looks like a number (integer or float)
        # Using simple heuristic: if it's composed of digits and optional dot
        is_numeric = val_str.replace('.', '', 1).isdigit()
        
        if is_numeric:
            # Clean column name for display (remove underscores, title case)
            clean_col = col_name.replace('_', ' ').title()
            return f"{clean_col} {val_str}"
        return val_str

    _MONTH_NAMES_ES = {
        1: 'Ene', 2: 'Feb', 3: 'Mar', 4: 'Abr',
        5: 'May', 6: 'Jun', 7: 'Jul', 8: 'Ago',
        9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dic',
    }

    @staticmethod
    def _format_period_label(period_obj, grain: TimeGrain):
        """
        Formats a period object (datetime) into a human-readable string based on grain.
        """
        if isinstance(period_obj, pd.Timestamp):
            if grain == TimeGrain.MONTH:
                month_es = IbisEngine._MONTH_NAMES_ES.get(period_obj.month, '???')
                return f"{month_es}-{period_obj.year}"
            elif grain == TimeGrain.YEAR:
                return period_obj.strftime('%Y') # e.g., 2021
            elif grain == TimeGrain.WEEK:
                return period_obj.strftime('%Y-W%W') # e.g., 2021-W10
            else: # Day or other
                return period_obj.strftime('%Y-%m-%d') # e.g., 2021-03-15
        return str(period_obj).split(' ')[0] # Fallback for non-datetime or other types

    @staticmethod
    def _is_temporal_column(t, col_name: str | None) -> bool:
        """Detecta si una columna del table Ibis es temporal."""
        if not col_name or col_name not in t.columns:
            return False
        try:
            col_type = str(t[col_name].type()).lower()
        except Exception:
            return False
        return 'timestamp' in col_type or 'date' in col_type

    @staticmethod
    def _is_numeric_column(t, col_name: str | None) -> bool:
        """Detecta si una columna del table Ibis es numérica/agregable."""
        if not col_name or col_name not in t.columns:
            return False
        try:
            col_type = str(t[col_name].type()).lower()
        except Exception:
            return False
        numeric_tokens = ('int', 'float', 'double', 'decimal', 'numeric')
        return any(token in col_type for token in numeric_tokens)

    @staticmethod
    def _normalize_metric_label(column_name: str | None) -> str:
        """Convierte snake_case técnico a label legible universal.

        Sin diccionarios estáticos ni condicionales fijos — usa patrones
        regex universales para detectar sufijos/preposiciones/unidades.
        """
        if not column_name:
            return "Valor"
        name = str(column_name).strip()
        tokens = re.split(r'_+', name)
        mapped: list[str] = []
        for t in tokens:
            tl = t.lower()
            if tl in ('s',):
                mapped.append('(S/)')
            elif tl in ('km',):
                mapped.append('Km')
            elif tl in ('id',):
                mapped.append('ID')
            elif tl in ('pct', 'porcentual'):
                mapped.append('(%)')
            elif tl in ('nro', 'n°', 'numero'):
                mapped.append('N°')
            elif tl in ('por',):
                mapped.append('por')
            elif tl in ('de',):
                mapped.append('de')
            elif tl in ('del',):
                mapped.append('del')
            elif tl in ('vs',):
                mapped.append('vs')
            elif tl in ('y',):
                mapped.append('y')
            else:
                mapped.append(t.capitalize())
        label = ' '.join(mapped).strip()
        if not label:
            return name.replace('_', ' ').title()
        return label

    @staticmethod
    def _pick_reference_date_column(intent, table_columns: list[str], exclude: str | None = None) -> str | None:
        """Selecciona la fecha de referencia más probable para métricas temporales derivadas."""
        candidates: list[str] = []

        date_column = getattr(intent, 'date_column', None)
        if date_column:
            candidates.append(date_column)

        for candidate in list(getattr(intent, 'group_by', None) or []):
            if candidate:
                candidates.append(candidate)

        dimension_candidate = getattr(intent, 'dimension', None)
        if dimension_candidate:
            candidates.append(dimension_candidate)

        candidates.extend([
            "fecha_de_stock",
            "fecha_stock",
            "fecha",
            "date",
            "periodo",
        ])

        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate == exclude or candidate in seen:
                continue
            seen.add(candidate)
            if candidate in table_columns:
                return candidate

        lowered_columns = [column for column in table_columns if column != exclude]
        for candidate in lowered_columns:
            normalized = candidate.lower()
            if "fecha" in normalized and ("stock" in normalized or "corte" in normalized or "period" in normalized):
                return candidate

        for candidate in lowered_columns:
            if "fecha" in candidate.lower():
                return candidate

        return None

    @staticmethod
    def _should_apply_latest_snapshot_filter(intent, table_columns: list[str], dataset_contract: dict | None = None) -> bool:
        return should_apply_latest_snapshot_filter(intent, table_columns, dataset_contract)

    @staticmethod
    def _normalize_filter_operator(operator) -> str:
        normalized = str(getattr(operator, "value", operator) or "").strip().lower()
        if normalized == "=":
            normalized = "=="
        return normalized

    @staticmethod
    def _coerce_filter_scalar(col_type: str, value):
        if value is None:
            return value

        is_date_col = 'timestamp' in col_type or 'date' in col_type
        if is_date_col and isinstance(value, str):
            if value.lower() in ['latest', 'last', 'ultimo', 'actual', 'recent', 'hoy']:
                return value
            try:
                from datetime import datetime as dt
                return dt.fromisoformat(value.strip())
            except ValueError:
                return value

        is_bool_col = 'boolean' in col_type or 'bool' in col_type
        if is_bool_col and isinstance(value, str):
            return value.strip().lower() in ('true', '1', 'yes', 'sí', 'si')

        is_numeric_col = any(k in col_type for k in ('int', 'float', 'double', 'decimal', 'numeric'))
        if is_numeric_col and isinstance(value, str):
            try:
                return int(value) if 'int' in col_type else float(value)
            except (ValueError, TypeError):
                return value

        return value

    @staticmethod
    def _sanitize_intent_projection(intent, available_columns: set, dataset_contract: dict | None = None) -> None:
        """
        [V1] Sanitización Universal de Proyecciones.

        Portal centralizado que limpia el intent ANTES de que llegue a
        cualquiera de los 7 métodos _analyze_*.  Garantiza que:
        1. group_by nunca contenga la dimensión primaria (evita
           'Duplicate column name' en DuckDB/Ibis).
        2. Filtros 'between' (no soportados por FilterOperator) se
           expandan a pares >= / <= que _build_filter_expression
           procesa nativamente.

        Es idempotente y seguro: si no hay colisión ni between,
        el intent no se modifica.
        """
        # ── 1. Escudo de Duplicados (group_by vs dimension) ──────────
        dim = getattr(intent, 'dimension', None)
        gb = list(getattr(intent, 'group_by', None) or [])

        if dim and gb and dim in gb:
            # [FIX 2026-07-04] Preservar dimensiones originales del usuario.
            # La columna en group_by es intencional (el Direction Guard o el usuario la
            # pusieron allí). Ibis/DuckDB maneja columnas duplicadas entre dimension y
            # group_by sin errores. NO eliminar la dimensión del usuario para evitar que
            # el guard sobrescriba la intención original del prompt.
            # Solo eliminar duplicados literales dentro de la misma lista.
            unique_gb: list = []
            seen: set = set()
            for col in gb:
                if col not in seen:
                    unique_gb.append(col)
                    seen.add(col)
            if len(unique_gb) < len(gb):
                intent.group_by = unique_gb
                print(
                    f"🧹 [IBIS SANITIZE] Duplicados en group_by removidos: "
                    f"{gb} → {unique_gb}"
                )
            else:
                print(
                    f"🧹 [IBIS SANITIZE] Dimensión '{dim}' también en group_by "
                    f"— preservando ambas (intención del usuario)"
                )

        # ── 2. Expansión de filtros 'between' ────────────────────────
        raw_filters = list(getattr(intent, 'filters', None) or [])
        sanitized_filters: list = []
        mutated = False
        for f in raw_filters:
            op = str(getattr(f, 'operator', '') or '').strip().lower()
            if op == 'between' and isinstance(getattr(f, 'value', None), str):
                parts = f.value.split(' and ')
                if len(parts) == 2:
                    lo, hi = parts[0].strip(), parts[1].strip()
                    sanitized_filters.append(
                        DataFilter(column=f.column, operator='>=', value=lo)
                    )
                    sanitized_filters.append(
                        DataFilter(column=f.column, operator='<=', value=hi)
                    )
                    print(
                        f"🧹 [IBIS SANITIZE] Filtro between expandido: "
                        f"'{f.column}' → ['>= {lo}', '<= {hi}']"
                    )
                    mutated = True
                    continue
            sanitized_filters.append(f)

        if mutated and hasattr(intent, 'filters'):
            intent.filters = sanitized_filters

    @staticmethod
    def _build_filter_expression(t, f: DataFilter):
        col = t[f.column]
        val = f.value
        raw_type = str(col.type())
        col_type = raw_type.lower()
        operator = IbisEngine._normalize_filter_operator(f.operator)

        print(f"🕵️ [IBIS SPY] Columna '{f.column}' | Tipo Crudo: '{raw_type}' | Normalizado: '{col_type}'")

        is_date_col = 'timestamp' in col_type or 'date' in col_type
        if is_date_col and isinstance(val, str) and val.lower() in ['latest', 'last', 'ultimo', 'actual', 'recent', 'hoy']:
            print(f"🧠 [IBIS] Traducción Simbólica activada para '{val}'...")
            try:
                val = t[f.column].max().to_pyarrow().as_py()
                print(f"   -> Fecha Resuelta Matemáticamente: {val}")
            except Exception as e:
                print(f"⚠️ Error en resolución simbólica: {e}")

        import re as _re
        if isinstance(val, str):
            # Regex case-insensitive con soporte de espacios y catálogo completo
            _agg_match = _re.match(
                r'^(max|min|avg|mean|sum|median|count|count_distinct|stddev|std|variance|var)\(\s*(\w+)\s*\)$',
                val.strip(),
                _re.IGNORECASE
            )
            if _agg_match:
                _agg_func_raw, _agg_col = _agg_match.group(1), _agg_match.group(2)
                _agg_func_lower = _agg_func_raw.lower()

                # Mapear a método Ibis usando el catálogo de clase
                _ibis_method = IbisEngine.IBIS_FUNC_MAP.get(_agg_func_lower)

                if _ibis_method and _agg_col in t.columns:
                    print(f"🧠 [IBIS] Resolviendo agregado '{_agg_func_raw}({_agg_col})' como filtro...")

                    # Validar que el método exista en la columna
                    if not hasattr(t[_agg_col], _ibis_method):
                        print(f"⚠️ [IBIS] Función '{_ibis_method}' no soportada para columna '{_agg_col}'")
                    else:
                        try:
                            _agg_result = getattr(t[_agg_col], _ibis_method)().to_pyarrow().as_py()
                            print(f"   -> {_agg_func_raw}({_agg_col}) = {_agg_result} (tipo: {type(_agg_result).__name__})")
                            if _agg_result is not None:
                                val = _agg_result
                        except Exception as _agg_e:
                            print(f"⚠️ Error resolviendo agregado '{_agg_func_raw}({_agg_col})': {_agg_e}")

        is_string_col = 'string' in col_type or 'utf8' in col_type or 'varchar' in col_type
        if isinstance(val, list):
            coerced_values = [IbisEngine._coerce_filter_scalar(col_type, item) for item in val]
        else:
            coerced_values = IbisEngine._coerce_filter_scalar(col_type, val)

        if operator in {"in", "not_in", "in_list"}:
            values = coerced_values if isinstance(coerced_values, list) else [coerced_values]
            if is_string_col:
                expr = col.upper().isin([str(item).upper() for item in values])
            else:
                expr = col.isin(values)
            return ~expr if operator == "not_in" else expr

        val = coerced_values
        if is_string_col and isinstance(val, str):
            if operator == "==":
                return col.upper() == val.upper()
            if operator == "!=":
                return col.upper() != val.upper()
            # [V2] Operadores de texto case-insensitive
            if operator in {"contains", "like", "ilike"}:
                # ilike y like se tratan como contains case-insensitive (DuckDB lo ejecuta nativamente)
                clean_val = val.strip("%") if isinstance(val, str) else val
                return col.upper().contains(clean_val.upper())
            if operator == "starts_with":
                return col.upper().startswith(val.upper())
            if operator == "ends_with":
                return col.upper().endswith(val.upper())
            if operator in {"not_contains", "not_like"}:
                return ~col.upper().contains(val.upper())
            # [V2] Fallback para operadores de texto no reconocidos:
            # Usar contains como aproximación segura y loguear la aproximación.
            print(f"⚠️ [IBIS FILTER] Operador de texto '{operator}' no reconocido. "
                  f"Aproximando con 'contains' para columna '{f.column}'.")
            return col.upper().contains(val.upper())
        else:
            if operator == "==": return col == val
            if operator == "!=": return col != val
            if operator == ">": return col > val
            if operator == "<": return col < val
            if operator == ">=": return col >= val
            if operator == "<=": return col <= val
            # [V2] Fallback para operadores numéricos no reconocidos: loguear y retornar None
            print(f"⚠️ [IBIS FILTER] Operador numérico '{operator}' no reconocido para columna "
                  f"'{f.column}'. Filtro descartado. Revisa el contrato semántico.")
        return None

    @staticmethod
    def _apply_intent_filters(t, intent):
        # [V2.2] Merge pre-loop: unificar positive_filters + filters
        # como defense in depth. positive_filters no es un campo del
        # modelo BaseIntent, pero si algún código futuro lo pasa en el
        # dict de creación, Pydantic v2 lo ignora silenciosamente.
        # Este merge lo captura y lo unifica antes del loop principal.
        all_filters = list(getattr(intent, "filters", []) or [])
        positive_filters = list(getattr(intent, "positive_filters", []) or [])
        if positive_filters:
            all_filters = positive_filters + all_filters
            print(f"🛡️ [INTENT FILTERS] positive_filters mergeado pre-loop: "
                  f"{len(positive_filters)} filtro(s) unificado(s) con {len(all_filters) - len(positive_filters)} existente(s)")

        for f in all_filters:
            expr = IbisEngine._build_filter_expression(t, f)
            if expr is not None:
                t = t.filter(expr)

        for f in list(getattr(intent, "negative_filters", []) or []):
            expr = IbisEngine._build_filter_expression(t, f)
            if expr is None:
                continue
            operator = IbisEngine._normalize_filter_operator(f.operator)
            if operator not in {"!=", "not_in"}:
                expr = ~expr
            t = t.filter(expr)
        return t

    @staticmethod
    def execute_plan(
        parquet_path: str,
        plan: AnalysisPlan,
        protected_cols: list = [],
        recipe_mode: bool = True,
        related_parquets: dict[str, str] | None = None,
    ) -> dict:
        # 1. Conexión de Alto Rendimiento (DuckDB In-Process)
        con = ibis.duckdb.connect()
        con.con.execute("SET memory_limit='2GB'")
        con.con.execute("SET threads=2")
        t = con.read_parquet(parquet_path)
        dataset_contract = DataEngine.load_semantic_contract(parquet_path)

        # [FASE 3 MULTI-HOJA] Cargar frames relacionados en la misma conexión
        related_tables: dict[str, Any] = {}
        if related_parquets:
            for frame_id, related_path in related_parquets.items():
                try:
                    related_t = con.read_parquet(related_path)
                    table_name = frame_id.replace("__", "_").replace(":", "_")
                    con.create_table(table_name, related_t, overwrite=True)
                    related_tables[frame_id] = con.table(table_name)
                    print(f"📋 [MULTI-HOJA] Frame relacionado cargado: {frame_id} → tabla '{table_name}'")
                except Exception as _rel_err:
                    print(f"⚠️ [MULTI-HOJA] Error cargando frame '{frame_id}': {_rel_err}")

        # [FASE 3C MULTI-HOJA] Pre-agregación primaria para datasets transaccionales
        # Si el plan especifica pre_aggregation (vía LLM o detector de cardinalidad),
        # consolidar la tabla primaria ANTES del JOIN. Esto reduce 43,800 filas
        # diarias a ~120 filas totales por entidad, evitando explosiones cartesianas.
        _agg_spec = getattr(plan, "pre_aggregation", None)
        if _agg_spec and _agg_spec.group_by and _agg_spec.metrics:
            _agg_func_name = getattr(_agg_spec, "aggregation", "sum") or "sum"
            _agg_func_name = IbisEngine.IBIS_FUNC_MAP.get(_agg_func_name, _agg_func_name)
            _agg_exprs = {}
            for _metric in _agg_spec.metrics:
                if _metric in t.columns:
                    _agg_exprs[_metric] = getattr(t[_metric], _agg_func_name)().name(_metric)
            if _agg_exprs:
                _before_rows = int(t.count().execute())
                t = t.group_by(_agg_spec.group_by).aggregate(**_agg_exprs)
                _after_rows = int(t.count().execute())
                print(f"📊 [MULTI-HOJA] Tabla PRIMARIA pre-agregada: "
                      f"{_before_rows} → {_after_rows} filas "
                      f"(GROUP BY {_agg_spec.group_by}, {_agg_func_name}({list(_agg_exprs.keys())}))")

        # [FASE 3B MULTI-HOJA] JOIN determinista con gobernanza por contrato
        # Capa 1: plan.join_keys (LLM vía _MULTI_SHEET_INSTRUCTION)
        # Capa 2: plan.join_keys heredadas del orchestrator (value overlap ratio)
        # Capa 3: Heurístico de nombres (id_, cod, key) como último recurso
        # Capa 4c: Guardián anti many-to-many post-JOIN (límite: max_rows × 10)
        plan_frame_ids = list(getattr(plan, "related_frame_ids", []) or [])
        if related_tables and plan_frame_ids:
            related_keys = list(related_tables.keys())
            _primary_rows = int(t.count().execute())
            for frame_id in plan_frame_ids:
                resolved_key = IbisEngine._resolve_frame_key(frame_id, related_keys)
                if resolved_key is None:
                    print(f"⚠️ [MULTI-HOJA] Frame '{frame_id}' solicitado en plan no encontrado en related_tables. Skip.")
                    continue
                related_table = related_tables[resolved_key]
                print(f"📋 [MULTI-HOJA] Resuelto: '{frame_id}' → '{resolved_key}'")

                # [FASE 3C MULTI-HOJA] Pre-agregación de tabla relacionada
                if _agg_spec and _agg_spec.group_by and _agg_spec.metrics:
                    _rel_agg_exprs = {}
                    for _metric in _agg_spec.metrics:
                        if _metric in related_table.columns:
                            _rel_agg_exprs[_metric] = getattr(
                                related_table[_metric], _agg_func_name
                            )().name(_metric)
                    if _rel_agg_exprs:
                        _rel_before = int(related_table.count().execute())
                        related_table = related_table.group_by(
                            _agg_spec.group_by
                        ).aggregate(**_rel_agg_exprs)
                        _rel_after = int(related_table.count().execute())
                        print(f"📊 [MULTI-HOJA] Tabla RELACIONADA '{frame_id}' pre-agregada: "
                              f"{_rel_before} → {_rel_after} filas")

                primary_cols = set(t.columns)
                related_cols = set(related_table.columns)
                join_key = None
                used_keys: list[str] = []

                # --- CAPA 1+2: Gobernanza por contrato (plan.join_keys) ---
                # ── ADR-JOIN-001 (Guardián de Integridad de Contratos) ─────
                # El LLM puede inyectar métricas flotantes como join_keys.
                # Una métrica (float, int, decimal) NO es un identificador:
                # produce many-to-many JOINs y explosiones cartesianas.
                # Filtrar: solo aceptar columnas categóricas (string-based).
                # ──────────────────────────────────────────────────────────
                _plan_join_keys = getattr(plan, "join_keys", []) or []
                if _plan_join_keys:
                    valid_keys = [k for k in _plan_join_keys if k in primary_cols and k in related_cols]
                    if valid_keys:
                        _join_schema = t.schema()
                        _categorical = [
                            k for k in valid_keys
                            if k in _join_schema and _join_schema[k].is_string()
                        ]
                        if _categorical:
                            join_key = _categorical[0]
                            used_keys = list(_categorical)
                            _rejected = set(valid_keys) - set(_categorical)
                            if _rejected:
                                print(f"⚠️ [MULTI-HOJA] Contrato filtrado: {_rejected} "
                                      f"rechazadas (métricas numéricas). "
                                      f"Usando: {_categorical}")
                            print(f"📋 [MULTI-HOJA] JOIN gobernado por contrato: '{join_key}'"
                                  f" (plan.join_keys={_categorical})")
                        else:
                            _type_hints = {
                                k: str(_join_schema[k]) for k in valid_keys
                                if k in _join_schema
                            }
                            print(f"🛑 [MULTI-HOJA] Contrato RECHAZADO: todas las "
                                  f"join_keys son métricas numéricas: {_type_hints}. "
                                  f"Delegando a heurístico categórico.")
                            # join_key queda None → cae a CAPA 3 heurístico
                    else:
                        print(f"⚠️ [MULTI-HOJA] join_keys del plan {_plan_join_keys} "
                              f"no existen en ambas tablas. Delegando a heurístico.")

                # --- CAPA 3: Heurístico de nombres (fallback) ---
                if join_key is None:
                    common_keys = [
                        col for col in (primary_cols & related_cols)
                        if col.lower().startswith("id_") or col.lower() == "id"
                        or "id" in col.lower() or "cod" in col.lower() or "key" in col.lower()
                    ]
                    if not common_keys:
                        common_keys = [col for col in (primary_cols & related_cols) if col in primary_cols]

                    # --- CAPA 3b: Guardián de tipos para JOIN (solo categóricas) ---
                    # Las columnas métricas (float, int, decimal) NO pueden usarse
                    # como llaves de JOIN porque producen productos cartesianos o
                    # joins semánticamente inválidos (una métrica no es un identificador).
                    if common_keys:
                        _join_schema = t.schema()
                        _string_keys = [
                            col for col in common_keys
                            if col in _join_schema and _join_schema[col].is_string()
                        ]
                        if _string_keys:
                            common_keys = _string_keys
                        else:
                            _type_hints = {
                                col: str(_join_schema[col])
                                for col in common_keys
                                if col in _join_schema
                            }
                            print(f"🛑 [MULTI-HOJA] JOIN abortado: "
                                  f"ninguna columna común es categórica. "
                                  f"Tipos detectados: {_type_hints}")
                            return {
                                "error": (
                                    f"Data Contract Violation: Join key must be categorical "
                                    f"(string-based). Las columnas comunes "
                                    f"({', '.join(str(k) for k in common_keys[:5])}) "
                                    f"son numéricas/métricas. "
                                    f"Usa una columna categórica como 'placa_unidad', "
                                    f"'tipo_unidad' u 'origen' como llave de cruce."
                                )
                            }

                    if common_keys:
                        join_key = common_keys[0]
                        used_keys = list(common_keys)
                        print(f"📋 [MULTI-HOJA] JOIN heurístico: '{join_key}' "
                              f"(detectado de {len(common_keys)} columnas comunes)")

                if join_key:
                    _related_rows = int(related_table.count().execute())
                    print(f"📋 [MULTI-HOJA] Aplicando LEFT JOIN: primary.{join_key} = {frame_id}.{join_key}"
                          f" ({_primary_rows}×{_related_rows} filas)")
                    left_cols = {col: t[col] for col in t.columns}
                    right_cols = {
                        f"{col}_{frame_id.replace('__', '_').replace(':', '_')}": related_table[col]
                        for col in related_cols
                        if col not in used_keys
                    }
                    joined = t.left_join(related_table, join_key)
                    all_cols = {**left_cols, **right_cols}
                    t = joined.select(**all_cols)

                    # --- CAPA 4c: Guardián anti many-to-many ---
                    _joined_rows = int(t.count().execute())
                    _max_allowed = max(_primary_rows, _related_rows) * 10
                    if _joined_rows > _max_allowed:
                        print(f"🛑 [MULTI-HOJA] JOIN con '{join_key}' produjo explosión: "
                              f"{_primary_rows}+{_related_rows} → {_joined_rows} filas "
                              f"(límite: {_max_allowed}). Abortando.")
                        return {
                            "error": (
                                f"El cruce de tablas con la columna '{join_key}' produjo "
                                f"{_joined_rows} filas ({_primary_rows} × {_related_rows}), "
                                f"lo que sugiere un producto cartesiano (many-to-many). "
                                f"Usa una columna con mayor unicidad como llave de JOIN "
                                f"(ej: un identificador único por registro)."
                            )
                        }
                    _primary_rows = _joined_rows

        # 🧬 [V7] UNIVERSAL SCHEMA INSPECTOR (Replaces hardcoded money_cols)
        print("\n" + "🔍" * 40)
        print(f"🔍 [IBIS V7] Schema-Agnostic Inspector")
        print("🔍" * 40)

        try:
            # A. Schema overview
            print(f"\n📊 [SCHEMA]:")
            schema = t.schema()
            print(schema)
            
            # B. Sample data
            print(f"\n👁️ [SAMPLE DATA - TOP 3]:")
            print(t.head(3).to_pandas().to_string())
            
            # C. Auto-cast string columns that are actually numeric
            # 🛡️ [FASE 4D] Passing protected_cols to prevent sabotage
            print(f"\n⚡ [AUTO-CASTING] (Protected: {len(protected_cols)} cols):")
            t = IbisEngine._auto_cast_columns(t, protected_cols)
            
        except Exception as inspector_e:
            print(f"⚠️ [INSPECTOR ERROR]: {inspector_e}")
        
        print("🔍" * 40 + "\n")

        # =====================================================================
        # 🛡️ [FASE 4] DATA SHIELD: Column Existence Validation
        # Last line of defense — if Gemini hallucinated a column name that
        # slipped past SemanticTranslator pre-flight, we catch it HERE
        # before any Ibis expression touches a phantom column.
        # =====================================================================
        available_columns = set(t.columns)
        intent = plan.main_intent
        
        def _validate_col(col_name: str, context: str) -> bool:
            """Returns True if column exists, logs warning and returns False otherwise."""
            if col_name and col_name in available_columns:
                return True
            print(f"🛡️ [DATA SHIELD] ¡Columna alucinada bloqueada! '{col_name}' no existe en el dataset. (Contexto: {context})")
            return False
        
        # A. Validate filter columns
        if intent.filters:
            validated_filters = []
            for f in intent.filters:
                if _validate_col(f.column, "filter"):
                    validated_filters.append(f)
            intent.filters = validated_filters

        if getattr(intent, "negative_filters", None):
            validated_negative_filters = []
            for f in intent.negative_filters:
                if _validate_col(f.column, "negative_filter"):
                    validated_negative_filters.append(f)
            intent.negative_filters = validated_negative_filters
        
        # B. Validate group_by columns (descriptive/distribution)
        if hasattr(intent, 'group_by') and intent.group_by:
            original_len = len(intent.group_by)
            intent.group_by = [c for c in intent.group_by if _validate_col(c, "group_by")]
            if not intent.group_by and original_len > 0:
                # All group_by columns were hallucinated — abort gracefully
                return {"error": f"Todas las columnas de agrupamiento fueron rechazadas por Data Shield. Columnas disponibles: {sorted(available_columns)}"}
        
        # C. Validate metric columns  
        if hasattr(intent, 'metrics') and intent.metrics:
            intent.metrics = [c for c in intent.metrics if _validate_col(c, "metrics")]
        
        # D. Validate date/value columns for trends and predictive
        if hasattr(intent, 'date_column') and intent.date_column:
            if not _validate_col(intent.date_column, "date_column"):
                return {"error": f"Columna temporal '{intent.date_column}' no existe. Columnas disponibles: {sorted(available_columns)}"}
        
        if hasattr(intent, 'value_column') and intent.value_column:
            if not _validate_col(intent.value_column, "value_column"):
                return {"error": f"Columna de valor '{intent.value_column}' no existe. Columnas disponibles: {sorted(available_columns)}"}

        for optional_metric_field in ("plot_metric", "ranking_metric"):
            metric_value = getattr(intent, optional_metric_field, None)
            if metric_value and not _validate_col(metric_value, optional_metric_field):
                setattr(intent, optional_metric_field, None)

        # E. Validate dimension column (distribution/diagnostic)
        if hasattr(intent, 'dimension') and intent.dimension:
            if not _validate_col(intent.dimension, "dimension"):
                return {
                    "error": f"Columna de dimensión '{intent.dimension}' no existe en el dataset. "
                             f"Columnas disponibles: {sorted(available_columns)}"
                }

        # F. Validate metric column (singular — distribution/diagnostic)
        if hasattr(intent, 'metric') and intent.metric:
            if not _validate_col(intent.metric, "metric"):
                _numeric_fallbacks = [
                    c for c in sorted(available_columns)
                    if not c.startswith('_') and c in t.columns and t[c].type().is_numeric()
                ]
                if _numeric_fallbacks:
                    _original_metric = intent.metric
                    intent.metric = _numeric_fallbacks[0]
                    print(
                        f"🔄 [DATA SHIELD] Métrica singular '{_original_metric}' "
                        f"alucinada → auto-corregida a '{intent.metric}'"
                    )
                else:
                    return {
                        "error": f"Métrica '{intent.metric}' no existe y no hay alternativas numéricas. "
                                 f"Columnas: {sorted(available_columns)}"
                    }

        print(f"✅ [DATA SHIELD] Validación de columnas completada. {len(available_columns)} columnas disponibles.")

        # ═══════════════════════════════════════════════════════════════
        # 🧹 IBIS SANITIZE — Sanitización Universal de Proyecciones
        # Punto único de intercepción que blinda los 7 métodos _analyze_*
        # contra colisiones de duplicidad y filtros no soportados.
        # ═══════════════════════════════════════════════════════════════
        IbisEngine._sanitize_intent_projection(intent, available_columns, dataset_contract)


        # ═══════════════════════════════════════════════════════════════
        # 🕐 TEMPORAL FORTRESS — Universal temporal filter normalization
        # Intercepta filtros de TODOS los planes (SIMPLE + COMPLEJO) y
        # corrige años ISO alucinados por el LLM antes de aplicar filtros.
        # ═══════════════════════════════════════════════════════════════
        if dataset_contract:
            temporal_schema: dict = {}
            evidence = dataset_contract.get('evidence', {})
            if isinstance(evidence, dict):
                max_year = _extract_year_from_any_string(evidence.get('max_date'))
                if max_year is not None:
                    temporal_schema['_dataset_year'] = max_year
                # [V9] Inyectar rango real de años desde datos para activar
                # el Fortress preventivo en resolve_temporal_filter_value.
                min_year = _extract_year_from_any_string(evidence.get('min_date'))
                if min_year is not None:
                    temporal_schema['_dataset_year_min'] = min_year
                if max_year is not None:
                    temporal_schema['_dataset_year_max'] = max_year
            for date_col in dataset_contract.get('date_columns', []) or []:
                if date_col not in temporal_schema:
                    temporal_schema[date_col] = {'type': 'temporal', 'role': 'date'}
            time_axis_col = str(dataset_contract.get('time_axis', '') or '').strip()
            if time_axis_col and time_axis_col not in temporal_schema:
                temporal_schema[time_axis_col] = {'type': 'temporal', 'role': 'date'}
            if temporal_schema:
                corrected = normalize_intent_temporal_filters(
                    intent, temporal_schema
                )
                if corrected is not intent:
                    intent = corrected
                    print(
                        "🕐 [TEMPORAL FORTRESS] Filtros temporales del intent "
                        "normalizados (corrección de año ISO)"
                    )


        # 2. Aplicar Filtros Globales (Si existen en la intención)
        snapshot_guard_applied = False
        if IbisEngine._should_apply_latest_snapshot_filter(intent, t.columns, dataset_contract):
            print("📸 [IBIS SNAPSHOT GUARD] Aplicando filtro automático is_latest_snapshot == True")
            t = t.filter(t['is_latest_snapshot'] == True)
            snapshot_guard_applied = True

        t = IbisEngine._apply_intent_filters(t, intent)

        # 3. Enrutador de Intenciones V7 (Schema-Agnostic)
        try:
            if intent.type == "trend":
                result = IbisEngine._analyze_trend(t, intent)
            elif intent.type == "descriptive":
                result = IbisEngine._analyze_descriptive(t, intent, recipe_mode=recipe_mode)
            elif intent.type == "distribution":
                result = IbisEngine._analyze_distribution(t, intent, plan=plan)
            elif intent.type == "diagnostic":
                result = IbisEngine._analyze_diagnostic(t, intent, plan=plan)
            elif intent.type == "predictive":
                result = IbisEngine._analyze_predictive(t, intent)
            else:
                return {"error": f"Intención '{intent.type}' no implementada aún en IbisEngine."}

            # 🧹 [FASE 1] Redondeo global — elimina decimales excesivos en TODA la salida
            rounded_result = IbisEngine._round_result(result)

            # FASE 5: Añadir el dataframe granular filtrado para Cross-Filtering local
            try:
                MAX_GRANULAR_ROWS = 100000
                row_count = int(t.count().execute())
                if row_count > MAX_GRANULAR_ROWS:
                    df_filtered = t.limit(MAX_GRANULAR_ROWS).to_pandas()
                    print(f"⚠️ [IBIS] Dataset truncado a {MAX_GRANULAR_ROWS} filas para cross-filter (total: {row_count})")
                else:
                    df_filtered = t.to_pandas()
                rounded_result['filtered_granular_df'] = df_filtered
            except Exception as e:
                print(f"⚠️ [IBIS] Error extrayendo filtered_granular_df: {e}")

            # [FIX 2026-06-??] Cross-filter snapshot inheritance (v2)
            # 1) Propagar bandera de que el snapshot guard se aplicó (legacy)
            # 2) Resolver la fecha máxima real de la columna temporal (time_axis)
            #    para que el canary executor inyecte la columna física en lugar
            #    del flag virtual is_latest_snapshot, que no siempre sobrevive
            #    la serialización Arrow en DuckDB-WASM.
            if snapshot_guard_applied:
                rounded_result['_snapshot_guard_applied'] = True
                time_axis = str(dataset_contract.get("time_axis") or "").strip()
                if time_axis and time_axis in t.columns:
                    try:
                        max_date_val = t[time_axis].max().execute()
                        if hasattr(max_date_val, 'strftime'):
                            max_date_str = max_date_val.strftime('%Y-%m-%d')
                        elif max_date_val is not None:
                            max_date_str = str(max_date_val)
                        else:
                            max_date_str = ""
                        if max_date_str:
                            rounded_result['_snapshot_resolved_date'] = {
                                "column": time_axis,
                                "value": max_date_str,
                            }
                    except Exception as e:
                        print(f"⚠️ [IBIS] Error resolving max snapshot date: {e}")

            return rounded_result
        except Exception as e:
            print(f"🔥 [IBIS ERROR] {e}")
            return {"error": f"Error de Ejecución Ibis: {str(e)}"}

    @staticmethod
    def _analyze_trend(t, intent: TimeTrendIntent):
        """
        [V8.2] Motor de 'Hard Facts' Evolutivos.
        Calcula: Crecimiento MoM/YoY, Tendencia, Picos, Valles.
        Soporta: Líneas, Áreas Apiladas, Multi-Series (Top N split).

        V6.5: Added split_dimension/split_limit for multi-series trend charts.
        When split_dimension is set, produces a pivoted line chart with one
        series per Top-N category, filtered by total volume sub-query.
        """
        plot_metric = getattr(intent, "plot_metric", None) or intent.value_column
        ranking_metric = getattr(intent, "ranking_metric", None) or plot_metric
        ranking_direction = str(getattr(intent, "ranking_direction", "desc") or "desc").lower()
        col_date = t[intent.date_column]
        col_val = t[plot_metric]
        col_rank = t[ranking_metric]
        
        # Selección de granularidad
        trunc_op = col_date.truncate('M') if intent.grain == TimeGrain.MONTH else \
                   col_date.truncate('W') if intent.grain == TimeGrain.WEEK else \
                   col_date.truncate('Y') if intent.grain == TimeGrain.YEAR else col_date.truncate('D')

        chart_type = intent.visual_protocol.value if intent.visual_protocol else "line"

        # ═══════════════════════════════════════════════════════════════════
        # V6.5: MULTI-SERIES PATH — Top N Dimensional Split
        # When split_dimension is provided, generate one line per top category.
        # Universal: works with any column name, any dataset domain.
        # ═══════════════════════════════════════════════════════════════════
        split_dim = getattr(intent, 'split_dimension', None)
        if split_dim and split_dim in [str(c) for c in t.columns]:
            split_limit = max(2, min(int(getattr(intent, 'split_limit', None) or 5), 15))
            top_n_aggregation_mode = str(getattr(intent, "top_n_aggregation_mode", "split") or "split").strip().lower()
            if top_n_aggregation_mode not in {"split", "sum"}:
                top_n_aggregation_mode = "split"
            print(
                f"📊 [IBIS TREND] Multi-series split: '{split_dim}' limit={split_limit} "
                f"mode={top_n_aggregation_mode}"
            )

            # Agregación segura según tipo de métrica
            met_type = str(col_val.type()).lower()
            agg_expr = col_val.count() if 'string' in met_type else col_val.sum()
            rank_type = str(col_rank.type()).lower()
            rank_agg_expr = col_rank.count() if 'string' in rank_type else col_rank.sum()

            # PASO 1: Sub-query Top N categorías por volumen total
            top_cats = (
                t.group_by(split_dim)
                .aggregate(vol_total=rank_agg_expr)
                .order_by(ibis.asc('vol_total') if ranking_direction == "asc" else ibis.desc('vol_total'))
                .limit(split_limit)
            )
            top_items = top_cats[split_dim].to_pandas().tolist()
            if not top_items:
                print(f"⚠️ [IBIS TREND] No categories found for split_dim '{split_dim}'")
                # Fall through to single-series below
            else:
                # PASO 2: Filtrar dataset temporal → solo Top N categorías
                t_split = t.filter(t[split_dim].isin(top_items))
                t_split = t_split.mutate(periodo=trunc_op)

                # PASO 3: Agrupar por [periodo, dimensión] y agregar
                agged_multi = (
                    t_split.group_by(['periodo', split_dim])
                    .aggregate(valor=agg_expr)
                    .order_by('periodo')
                )
                df_multi = agged_multi.to_pandas()

                if not df_multi.empty:
                    if top_n_aggregation_mode == "sum":
                        df_rollup = (
                            df_multi.groupby("periodo", as_index=False)["valor"]
                            .sum()
                            .sort_values("periodo")
                        )
                        chart_data = [
                            {
                                "name": IbisEngine._format_period_label(row["periodo"], intent.grain),
                                "value": float(row["valor"]),
                            }
                            for _, row in df_rollup.iterrows()
                        ]

                        total_by_cat = df_multi.groupby(split_dim)["valor"].sum().sort_values(ascending=False)
                        top_1 = total_by_cat.index[0] if len(total_by_cat) > 0 else "N/A"
                        top_1_val = float(total_by_cat.iloc[0]) if len(total_by_cat) > 0 else 0.0

                        hard_facts = {
                            "top_1_name": str(top_1),
                            "top_1_val": top_1_val,
                            "total_analyzed": float(total_by_cat.sum()),
                            "series_count": 1,
                            "rollup_dimension": split_dim,
                            "rollup_group_size": len(top_items),
                            "split_limit": split_limit,
                            "top_n_aggregation_mode": "sum",
                            "ranking_metric": ranking_metric,
                            "plot_metric": plot_metric,
                            "total_periods": len(df_rollup),
                        }

                        print(
                            f"✅ [IBIS TREND] Top-N rollup sum: {len(top_items)} categorías -> "
                            f"1 serie × {len(df_rollup)} periodos"
                        )

                        return {
                            "type": "echarts",
                            "chart_type": chart_type,
                            "data": chart_data,
                            "x_axis": "periodo",
                            "y_axis": plot_metric,
                            "title": f"Evolución de {plot_metric} (Suma Top {split_limit} {split_dim})",
                            "hard_facts": hard_facts,
                        }

                    # PASO 4: Pivotar → formato multi-serie para ChartFactory
                    df_pivot = df_multi.pivot_table(
                        index='periodo',
                        columns=split_dim,
                        values='valor',
                        aggfunc='sum',
                    ).fillna(0)
                    df_pivot = df_pivot.sort_index()

                    # Format period labels
                    period_labels = [
                        IbisEngine._format_period_label(p, intent.grain)
                        for p in df_pivot.index
                    ]

                    # Build multi-series chart data (same pivot format as Distribution multi-dim)
                    chart_data = []
                    for idx, (period_raw, row) in enumerate(df_pivot.iterrows()):
                        row_dict = {"name": period_labels[idx]}
                        for cat_name in df_pivot.columns:
                            row_dict[str(cat_name)] = float(row[cat_name])
                        chart_data.append(row_dict)

                    # Hard facts from the aggregated total
                    total_by_cat = df_multi.groupby(split_dim)['valor'].sum().sort_values(ascending=False)
                    top_1 = total_by_cat.index[0] if len(total_by_cat) > 0 else "N/A"
                    top_1_val = float(total_by_cat.iloc[0]) if len(total_by_cat) > 0 else 0

                    # ═══════════════════════════════════════════════════════════
                    # [V4] SERIES STATS — Estadísticas individuales por serie
                    # Garantiza que la narrativa de Gemini describa TODAS las
                    # series del gráfico, no solo el top_1. Schema-agnostic:
                    # funciona con cualquier dominio y cualquier número de series.
                    # ═══════════════════════════════════════════════════════════
                    series_stats: list[dict] = []
                    for cat_name in df_pivot.columns:
                        cat_series = df_pivot[cat_name]
                        s_start = float(cat_series.iloc[0])
                        s_end = float(cat_series.iloc[-1])
                        s_growth = round(
                            ((s_end - s_start) / s_start * 100) if s_start != 0 else 0.0, 2
                        )
                        s_peak_idx = cat_series.idxmax()
                        s_trough_idx = cat_series.idxmin()
                        series_stats.append({
                            "name": str(cat_name),
                            "total": float(cat_series.sum()),
                            "start_val": s_start,
                            "end_val": s_end,
                            "growth_pct": s_growth,
                            "trend": "Creciente" if s_growth > 0 else "Decreciente" if s_growth < 0 else "Estable",
                            "peak_period": IbisEngine._format_period_label(s_peak_idx, intent.grain),
                            "peak_value": float(cat_series.loc[s_peak_idx]),
                            "trough_period": IbisEngine._format_period_label(s_trough_idx, intent.grain),
                            "trough_value": float(cat_series.loc[s_trough_idx]),
                        })

                    hard_facts = {
                        "top_1_name": str(top_1),
                        "top_1_val": top_1_val,
                        "total_analyzed": float(total_by_cat.sum()),
                        "series_count": len(df_pivot.columns),
                        "total_periods": len(df_pivot),
                        "split_dimension": split_dim,
                        "split_limit": split_limit,
                        "top_n_aggregation_mode": "split",
                        "ranking_metric": ranking_metric,
                        "plot_metric": plot_metric,
                        "series_stats": series_stats,
                    }

                    print(
                        f"✅ [IBIS TREND] Multi-series: {len(df_pivot.columns)} series × "
                        f"{len(df_pivot)} periodos (limit={split_limit})"
                    )

                    return {
                        "type": "echarts",
                        "chart_type": chart_type,
                        "data": chart_data,
                        "x_axis": "periodo",
                        "y_axis": plot_metric,
                        "title": f"Evolución de {plot_metric} por {split_dim} (Top {split_limit})",
                        "hard_facts": hard_facts,
                        "barmode": "grouped",  # Signal to ChartFactory for multi-series rendering
                    }

        # ═══════════════════════════════════════════════════════════════════
        # SINGLE-SERIES PATH (Original V8.2 — unchanged)
        # ═══════════════════════════════════════════════════════════════════

        # 1. Extracción Pesada (Ibis)
        t = t.mutate(periodo=trunc_op)
        # [FIX V2.2] Soporte volumétrico: count() para strings, sum() para numéricos
        met_type_single = str(col_val.type()).lower()
        agg_expr_single = col_val.count() if 'string' in met_type_single else col_val.sum()
        agged = (t.group_by('periodo')
                 .aggregate(valor=agg_expr_single)
                 .order_by('periodo'))
        df_res = agged.to_pandas()

        # 🛡️ [V2] Guard de DataFrame Vacío — Previene crash de argmax/idxmax/iloc
        # sobre secuencias vacías. Devuelve un error estructurado que el orquestador
        # interpreta como 'empty_result' y muestra al usuario un mensaje amigable.
        if df_res.empty:
            filters_repr = [
                f"{getattr(f, 'column', '?')} {getattr(f, 'operator', '?')} '{getattr(f, 'value', '?')}'"
                for f in list(getattr(intent, 'filters', []) or [])
            ]
            print(f"🔇 [IBIS TREND] DataFrame vacío tras filtros: {filters_repr}")
            return {
                "error": "empty_result",
                "message": (
                    "No se encontraron registros con los filtros aplicados. "
                    "Verifica que el valor del filtro exista exactamente en los datos "
                    f"(filtros: {filters_repr})."
                ),
                "filters_applied": filters_repr,
            }

        # 2. Inyección de HARD FACTS (Python/Pandas)
        # MoM: variación porcentual respecto al periodo anterior
        df_res['_growth'] = df_res['valor'].pct_change().fillna(0) * 100
        
        # 🔮 [PHASE 2] YoY: variación porcentual respecto al mismo periodo del año anterior
        has_yoy = len(df_res) >= 12
        if has_yoy:
            # Shift by 12 periods for monthly, 52 for weekly, 1 for yearly
            yoy_shift = 12 if intent.grain == TimeGrain.MONTH else \
                        52 if intent.grain == TimeGrain.WEEK else \
                        1 if intent.grain == TimeGrain.YEAR else 365
            df_res['_yoy'] = df_res['valor'].pct_change(periods=yoy_shift).fillna(0) * 100
        
        overall_growth = ((df_res['valor'].iloc[-1] - df_res['valor'].iloc[0]) / df_res['valor'].iloc[0] * 100) if len(df_res) > 1 else 0
        trend_direction = "Creciente" if overall_growth > 0 else "Decreciente"
        
        # 📊 [PHASE 2] Peak & Trough detection
        peak_idx = df_res['valor'].idxmax()
        trough_idx = df_res['valor'].idxmin()
        peak_period = IbisEngine._format_period_label(df_res.loc[peak_idx, 'periodo'], intent.grain)
        trough_period = IbisEngine._format_period_label(df_res.loc[trough_idx, 'periodo'], intent.grain)
        peak_value = float(df_res.loc[peak_idx, 'valor'])
        trough_value = float(df_res.loc[trough_idx, 'valor'])
        
        chart_data = []
        for _, row in df_res.iterrows():
            extra = {"growth": f"{row['_growth']:.1f}%"}
            if has_yoy:
                extra["yoy"] = f"{row['_yoy']:.1f}%"
            chart_data.append({
                "name": IbisEngine._format_period_label(row['periodo'], intent.grain), 
                "value": float(row['valor']),
                "extra_info": extra
            })
        
        hard_facts = {
            "start_val": float(df_res['valor'].iloc[0]),
            "end_val": float(df_res['valor'].iloc[-1]),
            "overall_growth_pct": round(overall_growth, 2),
            "trend": trend_direction,
            "peak_period": peak_period,
            "peak_value": peak_value,
            "trough_period": trough_period,
            "trough_value": trough_value,
            "total_periods": len(df_res),
            "plot_metric": plot_metric,
        }
        
        if has_yoy:
            yoy_avg = df_res['_yoy'].replace([float('inf'), float('-inf')], 0).mean()
            hard_facts["yoy_avg_pct"] = round(yoy_avg, 2)
            hard_facts["yoy_available"] = True
        
        return {
            "type": "echarts",
            "chart_type": chart_type,
            "data": chart_data,
            "x_axis": "periodo",
            "y_axis": plot_metric,
            "title": f"Evolución de {plot_metric}",
            "hard_facts": hard_facts
        }

    @staticmethod
    def _analyze_descriptive(t, intent: DescriptiveIntent, recipe_mode: bool = False):
        """
        [V6.4] Motor de 'Hard Facts' Estructurales.
        Calcula: Share, Ranking, Top 1 vs Promedio.
        Soporta: Barras, Pie, Waterfall.
        """
        # ═══════════════════════════════════════════════════════════════
        # 🛡️ Guard: Métricas vacías (post-sanitización por Data Shield)
        # Si todas las métricas del plan fueron alucinadas y limpiadas,
        # intent.metrics queda []. Sin este guard:
        #   L1226: intent.metrics[0] → IndexError
        #   L1231: .aggregate([]) → DuckDB "SELECT clause without selection list"
        # ═══════════════════════════════════════════════════════════════
        if not intent.metrics:
            _avail_numeric = sorted(
                c for c in t.columns
                if not c.startswith('_') and t[c].type().is_numeric()
            )
            return {
                "error": "empty_metrics",
                "message": (
                    "Las métricas solicitadas no existen en este dataset. "
                    f"Columnas numéricas disponibles: {_avail_numeric}"
                ),
            }

        exprs = []
        for m in intent.metrics:
            col = t[m]
            try: 
                if 'String' in str(col.type()): col = col.cast('float64') 
            except: pass
            
            if intent.aggregation == "sum": exprs.append(col.sum().name(m))
            elif intent.aggregation == "avg": exprs.append(col.mean().name(m))
            elif intent.aggregation == "count": exprs.append(col.count().name(m))
            elif intent.aggregation == "max": exprs.append(col.max().name(m))
            elif intent.aggregation == "min": exprs.append(col.min().name(m))

        # Guard secundario: si ninguna expresión se construyó (ej: aggregation desconocida)
        if not exprs:
            return {
                "error": "empty_metrics",
                "message": "No se pudieron construir expresiones de agregación válidas.",
            }
        
        # 🚀 CASO A: DASHBOARD AGRUPADO (Estructural)
        if intent.group_by:
            primary_metric = intent.metrics[0]
            dimension_col = intent.group_by[0]
            
            # 1. Extracción Pesada (Ibis)
            agged = (t.group_by(intent.group_by)
                     .aggregate(exprs)
                     .order_by(ibis.desc(primary_metric))
                     .limit(15))
            
            chart_type = intent.visual_protocol.value if intent.visual_protocol else ("pie" if len(intent.group_by) <= 5 else "bar")
            
            recipe_sql = None
            if recipe_mode:
                try:
                    recipe_sql = str(ibis.to_sql(agged, dialect="duckdb"))
                except Exception:
                    recipe_sql = str(ibis.to_sql(agged))
            
            df_res = agged.to_pandas()
            
            # 🛡️ [V2] Guard de DataFrame Vacío en modo agrupado
            if df_res.empty:
                filters_repr = [
                    f"{getattr(f, 'column', '?')} {getattr(f, 'operator', '?')} '{getattr(f, 'value', '?')}'"
                    for f in list(getattr(intent, 'filters', []) or [])
                ]
                print(f"🔇 [IBIS DESCRIPTIVE] DataFrame vacío tras filtros: {filters_repr}")
                return {
                    "error": "empty_result",
                    "message": (
                        "No se encontraron registros con los filtros aplicados. "
                        "Verifica que el valor del filtro exista exactamente en los datos "
                        f"(filtros: {filters_repr})."
                    ),
                    "filters_applied": filters_repr,
                }
            
            # 2. Inyección de HARD FACTS (Python/Pandas)
            total_val = df_res[primary_metric].sum()
            df_res['_share'] = (df_res[primary_metric] / total_val * 100).round(1) # % del total
            df_res['_rank'] = range(1, len(df_res) + 1) # Ranking 1, 2, 3...
            
            # 3. Selección del Gráfico (Protocolo Visual del PDF)
            # Si el Cerebro ordenó un protocolo específico, lo respetamos.
            # Si no, aplicamos la regla por defecto (<5 Pie, >5 Barras).
            chart_type = intent.visual_protocol.value if intent.visual_protocol else ("pie" if len(df_res) <= 5 else "bar")
            
            # Preparar datos enriquecidos para el Frontend y el Narrador
            # 🛡️ [DATA SAFETY] Detección de Sufijo de Unidad
            # Si NO es moneda, asumimos que son "Unidades" (o lo que diga el usuario si pudiéramos saberlo, por ahora genérico)
            unit_suffix = ""
            if not getattr(intent, 'metric_unit', None) == 'currency':
                unit_suffix = " unidades"

            # 🎯 [FASE 3B] Detect secondary metric for dual-axis charts
            secondary_metric = intent.metrics[1] if len(intent.metrics) > 1 else None
            
            chart_data = []
            for _, row in df_res.iterrows():
                extra = {
                    "share": f"{row['_share']}%",
                    "rank": f"#{row['_rank']}",
                    "unit_suffix": unit_suffix
                }
                # Dual-axis: include secondary metric value
                if secondary_metric and secondary_metric in df_res.columns:
                    extra["secondary_value"] = float(row[secondary_metric])
                
                # 🗣️ [FASE 4D] Context Injection
                clean_name = IbisEngine._format_chart_name(dimension_col, row[dimension_col])
                
                chart_data.append({
                    "name": clean_name, 
                    "value": float(row[primary_metric]),
                    "extra_info": extra
                })
            
            # Preparar datos enriquecidos para el Frontend y el Narrador
            
            response_payload = {
                "type": "echarts",
                "chart_type": chart_type,
                "data": chart_data,
                "x_axis": dimension_col,
                "y_axis": primary_metric,
                "title": f"Desglose de {primary_metric} por {dimension_col}",
                "hard_facts": { # Resumen Ejecutivo Automático
                    "top_1_name": str(df_res.iloc[0][dimension_col]),
                    "top_1_val": float(df_res.iloc[0][primary_metric]),
                    "top_1_share": float(df_res.iloc[0]['_share']),
                    "total_analyzed": float(total_val)
                }
            }
            
            if recipe_sql:
                response_payload["recipe_sql"] = recipe_sql
                response_payload["recipe_visual_protocol"] = chart_type
                
            return response_payload
            
        # 🚀 CASO B: KPI SOLITARIO (Waterfall o Simple)
        else:
            res = t.aggregate(exprs).to_pandas()
            data_dict = res.to_dict(orient='records')[0]
            # Si el protocolo pide Waterfall (Ej: P&L), el frontend lo manejará con estos datos
            chart_type = intent.visual_protocol.value if intent.visual_protocol else "kpi"
            
            return {"type": "kpi", "chart_type": chart_type, "data": data_dict}

    @staticmethod
    def _calculate_boxplot_stats(t, metric_col, dimension_col):
        """
        Calcula estadísticas de caja (Min, Q1, Mediana, Q3, Max) por dimensión.
        Retorna: DataFrame listo para ECharts boxplot.
        """
        try:
            # 1. Agrupar por dimensión
            grouped = t.group_by(dimension_col)
            
            # 2. Calcular estadísticos usando approx_quantile (más rápido que exacto)
            # Nota: Ibis no siempre tiene 'percentile' nativo en todos los backends, 
            # pero approx_quantile suele estar soportado.
            # Si falla, usaremos una aproximación manual o fallback.
            
            # Estructura objetivo para ECharts: [min, Q1, median, Q3, max]
            stats = grouped.aggregate(
                min=metric_col.min(),
                q1=metric_col.approx_quantile(0.25),
                median=metric_col.approx_quantile(0.50),
                q3=metric_col.approx_quantile(0.75),
                max=metric_col.max()
            )
            
            return stats.to_pandas()
            
        except Exception as e:
            print(f"⚠️ [IBIS] Error calculando Boxplot: {e}")
            return None

    @staticmethod
    def _analyze_funnel_conversion(t, metric_col, dimension_col, limit: int | None = None):
        """
        Calcula el embudo de conversión.
        Asume que 'dimension_col' define las etapas.
        """
        try:
            effective_limit = max(3, min(int(limit or 10), 15))

            dimension_key = (
                dimension_col
                if isinstance(dimension_col, str)
                else getattr(dimension_col, "get_name", lambda: str(dimension_col))()
            )

            # 1. Agrupar y sumar métrica (ej: count(id) o sum(monto))
            agged = t.group_by(dimension_col).aggregate(valor=metric_col.sum())
            res = agged.to_pandas()
            if res.empty:
                return res

            if dimension_key not in res.columns:
                dim_candidates = [column_name for column_name in res.columns if str(column_name) != "valor"]
                if dim_candidates:
                    dimension_key = dim_candidates[0]

            res['valor'] = pd.to_numeric(res['valor'], errors='coerce').fillna(0.0)
            res = res[res['valor'] > 0]
            if res.empty:
                return res
            
            # 2. Ordenar (Idealmente por un orden lógico de etapas, si no, descendente)
            # Por defecto: Descendente (el embudo se estrecha)
            res = res.sort_values(by='valor', ascending=False)

            # 2.1 Cap de cardinalidad para legibilidad enterprise
            if len(res) > effective_limit:
                head_count = max(effective_limit - 1, 1)
                top = res.head(head_count).copy()
                tail = res.iloc[head_count:]
                tail_sum = float(pd.to_numeric(tail['valor'], errors='coerce').fillna(0).sum())
                if tail_sum > 0:
                    top = pd.concat(
                        [
                            top,
                            pd.DataFrame([{dimension_key: "OTROS", "valor": tail_sum}]),
                        ],
                        ignore_index=True,
                    )
                res = top
            
            # 3. Calcular conversión relativa al paso anterior (o al primero)
            if not res.empty:
                max_val = res['valor'].max()
                if max_val > 0:
                    res['conversion_rate'] = (res['valor'] / max_val * 100).round(1)
                else:
                    res['conversion_rate'] = 0.0
            
            return res
            
        except Exception as e:
            print(f"⚠️ [IBIS] Error calculando Funnel: {e}")
            return None

    @staticmethod
    def _analyze_distribution(t, intent: DistributionIntent, plan: AnalysisPlan | None = None):
        """
        [V6.4] Motor de 'Hard Facts' Estadísticos.
        Calcula: Pareto (80/20), Concentración.
        Soporta: Histogramas, Barras de Frecuencia.
        """
        chart_data = []
        plot_metric = getattr(intent, "plot_metric", None) or intent.metric
        ranking_metric = getattr(intent, "ranking_metric", None) or plot_metric
        ranking_direction = str(getattr(intent, "ranking_direction", "desc") or "desc").lower()
        col_dim = t[intent.dimension]
        col_met = t[plot_metric]
        col_rank = t[ranking_metric]

        # [FASE 4E COMPARISON ENGINE] Distribución comparativa
        _dist_counterpart = None
        for _col in t.columns:
            if (_col.startswith(f"{plot_metric}_sheet__")
                    or _col.startswith(f"{plot_metric}_related_")):
                _dist_counterpart = _col
                break
        _dist_is_comparison = False
        _year_from_label = "Base"
        _year_to_label = "Comparado"
        if _dist_counterpart:
            _dist_is_comparison = True
            _original_plot_metric = plot_metric
            _original_ranking_metric = ranking_metric
            t = t.mutate(_distribution_variation=t[_dist_counterpart] - t[plot_metric])
            plot_metric = "_distribution_variation"
            col_met = t[plot_metric]
            col_rank = t[plot_metric]
            ranking_metric = plot_metric
            
            _suffix = _dist_counterpart[len(_original_plot_metric):]
            for _sep in ("_sheet__", "_related_"):
                if _sep in _suffix:
                    _year_to_label = _suffix.split(_sep, 1)[-1]
                    break
            if plan and hasattr(plan, 'primary_frame_id'):
                _pf = plan.primary_frame_id or ""
                _pf_parts = _pf.split("::")
                _from = _pf_parts[-1] if len(_pf_parts) > 1 else _pf
                if _from and _from != "primary":
                    _year_from_label = _from

            print(f"📊 [IBIS][DISTRIBUTION·COMPARISON] Variación detectada: {_dist_counterpart} - {plot_metric}")

        visual_protocol = getattr(intent, 'visual_protocol', None)
        visual_type = (
            str(visual_protocol.value).lower()
            if hasattr(visual_protocol, 'value')
            else str(visual_protocol or '').lower()
        )

        has_secondary_dim = bool(getattr(intent, 'group_by', None))
        sec_dim = intent.group_by[0] if has_secondary_dim else None

        # Guardrails de cardinalidad (no rompe contrato: solo limita ruido visual)
        if not isinstance(intent.limit, int) or intent.limit <= 0:
            intent.limit = 10
        if not _dist_is_comparison:
            intent.limit = min(intent.limit, 30)

        dim_type = str(col_dim.type()).lower()
        is_string_dim = 'string' in dim_type or 'utf8' in dim_type or 'varchar' in dim_type
        if is_string_dim and not _dist_is_comparison:
            try:
                unique_count = t.select(intent.dimension).distinct().count().execute()
                print(f"📊 [DISTRIBUTION] Dimensión '{intent.dimension}' es String con {unique_count} valores únicos")
                if unique_count > 200:
                    intent.limit = min(intent.limit, 12)
                elif unique_count > 80:
                    intent.limit = min(intent.limit, 15)
                elif unique_count > 40:
                    intent.limit = min(intent.limit, 20)
                elif unique_count <= 30 and not getattr(intent, 'ranking_metric', None):
                    # [FIX 2026-07-01] Regla de Densidad Estándar:
                    # mostrar TODOS los valores (hasta 30) cuando no hay
                    # ranking_metric explícito (si lo hay, usuario pidió
                    # Top N → mantener su limit).
                    intent.limit = unique_count
            except Exception as card_e:
                print(f"⚠️ [DISTRIBUTION] Error detectando cardinalidad: {card_e}")

        # 🛡️ Agregación segura según tipo de métrica
        if 'string' in str(col_met.type()).lower():
            agg_expr = col_met.count()
        else:
            agg_expr = col_met.sum()
        if 'string' in str(col_rank.type()).lower():
            rank_agg_expr = col_rank.count()
        else:
            rank_agg_expr = col_rank.sum()
        rank_order = ibis.asc('rank_val') if ranking_direction == "asc" else ibis.desc('rank_val')

        # --- BOXPLOT ---
        if visual_type in ['boxplot', 'boxplot_chart']:
            # ── ADR-VISUAL-001 ──────────────────────────────────────────
            # Boxplot Degeneration Guard: boxplot requires >1 observation
            # per group to produce meaningful quartile statistics. On
            # pre-aggregated JOIN data (e.g., 120 vehicles × 1 row each),
            # min=q1=median=q3=max — the chart is visually useless and
            # gets destructively downgraded to smart_table.
            # Guard: count groups with >1 obs; if zero → fallback to bar.
            # ───────────────────────────────────────────────────────────
            _boxplot_viable = True
            try:
                _obs_per_group = (
                    t.group_by(intent.dimension)
                    .aggregate(_n=col_met.count())
                    .filter(ibis.literal(True).ifelse(ibis._._n > 1, False))
                )
                _multi_obs_count = _obs_per_group.count().execute()
                if _multi_obs_count == 0:
                    _boxplot_viable = False
                    print(f"⚠️ [IBIS][BOXPLOT] Degenerado: cada '{intent.dimension}' tiene ≤1 observación → fallback a bar_chart")
            except Exception as _bp_err:
                # If the viability check itself fails, try a simpler approach
                try:
                    _total_rows = t.count().execute()
                    _unique_dims = t.select(intent.dimension).distinct().count().execute()
                    if _unique_dims >= _total_rows:
                        _boxplot_viable = False
                        print(f"⚠️ [IBIS][BOXPLOT] Degenerado (fallback check): {_unique_dims} grupos, {_total_rows} filas → fallback a bar_chart")
                except Exception:
                    pass

            if _boxplot_viable:
                print(f"   📊 [IBIS] Calculando Estadísticas de Caja para '{intent.dimension}'...")
                stats_df = IbisEngine._calculate_boxplot_stats(t, col_met, col_dim)
                if stats_df is not None:
                    return {
                        "type": "echarts",
                        "chart_type": "boxplot",
                        "data": stats_df.to_dict(orient='records'),
                        "title": f"Distribución de {intent.metric} por {intent.dimension}",
                    }
                return {"error": "Error calculando Boxplot"}
            # else: fall through to standard bar/distribution flow below

        # --- FUNNEL ---
        if visual_type in ['funnel', 'funnel_chart']:
            print(f"   🌪️ [IBIS] Calculando Conversión de Embudo para '{intent.dimension}'...")
            funnel_df = IbisEngine._analyze_funnel_conversion(
                t,
                col_met,
                intent.dimension,
                limit=min(intent.limit or 10, 15),
            )
            if funnel_df is not None and not funnel_df.empty:
                return {
                    "type": "echarts",
                    "chart_type": "funnel",
                    "data": funnel_df.to_dict(orient='records'),
                    "title": f"Embudo de {intent.metric} por {intent.dimension}",
                    "hard_facts": {
                        "analysis_type": "distribution_funnel",
                        "stage_count": int(len(funnel_df)),
                    },
                }
            return {"error": "Error calculando Funnel"}

        # --- HISTOGRAM ---
        if visual_type in ['histogram', 'histogram_chart']:
            print(f"   📊 [IBIS] Preparando distribución cruda para Histogram de '{plot_metric}'...")
            raw_df = t.select(col_met.name(plot_metric)).to_pandas()
            numeric_values = pd.to_numeric(raw_df[plot_metric], errors='coerce').dropna().tolist()
            if numeric_values:
                return {
                    "type": "echarts",
                    "chart_type": "histogram",
                    "data": numeric_values,
                    "title": f"Distribución de {plot_metric}",
                    "hard_facts": {
                        "analysis_type": "distribution_histogram",
                        "sample_size": len(numeric_values),
                    }
                }
            return {"error": "Histogram requiere una métrica numérica con datos crudos válidos."}

        # --- HEATMAP ---
        if visual_type in ['heatmap', 'heatmap_chart']:
            if not sec_dim:
                return {"error": "Heatmap requiere dos dimensiones y una métrica de intensidad."}

            print(f"   🔥 [IBIS] Preparando matriz de intensidad {intent.dimension} x {sec_dim}...")
            heatmap_df = (
                t.group_by([intent.dimension, sec_dim])
                .aggregate(valor=agg_expr)
                .to_pandas()
            )
            if heatmap_df.empty:
                return {"error": "Heatmap sin datos suficientes para renderizar."}

            heatmap_df['valor'] = pd.to_numeric(heatmap_df['valor'], errors='coerce').fillna(0.0)
            heatmap_df = heatmap_df[heatmap_df['valor'] != 0]
            if heatmap_df.empty:
                return {"error": "Heatmap sin intensidad válida para renderizar."}

            # Cap de ejes para evitar render saturado/ilegible
            max_x, max_y = 12, 12
            x_unique = heatmap_df[intent.dimension].nunique(dropna=True)
            y_unique = heatmap_df[sec_dim].nunique(dropna=True)

            if x_unique > max_x:
                x_dt = pd.to_datetime(heatmap_df[intent.dimension], errors='coerce')
                if float(x_dt.notna().mean()) >= 0.7:
                    heatmap_df = heatmap_df.assign(__x_dt=x_dt).dropna(subset=['__x_dt'])
                    keep_dates = sorted(heatmap_df['__x_dt'].unique())[-max_x:]
                    heatmap_df = heatmap_df[heatmap_df['__x_dt'].isin(keep_dates)]
                    heatmap_df[intent.dimension] = heatmap_df['__x_dt'].dt.strftime('%Y-%m-%d')
                    heatmap_df = heatmap_df.drop(columns=['__x_dt'])
                else:
                    top_x = (
                        heatmap_df.groupby(intent.dimension)['valor']
                        .sum()
                        .abs()
                        .sort_values(ascending=False)
                        .head(max_x)
                        .index
                    )
                    heatmap_df = heatmap_df[heatmap_df[intent.dimension].isin(top_x)]

            if y_unique > max_y:
                top_y = (
                    heatmap_df.groupby(sec_dim)['valor']
                    .sum()
                    .abs()
                    .sort_values(ascending=False)
                    .head(max_y)
                    .index
                )
                heatmap_df = heatmap_df[heatmap_df[sec_dim].isin(top_y)]

            if heatmap_df.empty:
                return {"error": "Heatmap filtrado por legibilidad quedó sin celdas válidas."}

            chart_data = []
            for _, row in heatmap_df.iterrows():
                chart_data.append(
                    {
                        intent.dimension: IbisEngine._format_chart_name(intent.dimension, row[intent.dimension]),
                        sec_dim: IbisEngine._format_chart_name(sec_dim, row[sec_dim]),
                        "valor": float(row['valor']),
                    }
                )
            return {
                "type": "echarts",
                "chart_type": "heatmap",
                "data": chart_data,
                "x_label": intent.dimension,
                "y_label": sec_dim,
                "title": f"Intensidad de {intent.metric} por {intent.dimension} y {sec_dim}",
                "hard_facts": {
                    "analysis_type": "distribution_heatmap",
                    "cell_count": len(chart_data),
                    "x_cardinality_capped": bool(x_unique > max_x),
                    "y_cardinality_capped": bool(y_unique > max_y),
                },
            }

        if not has_secondary_dim:
            if _dist_is_comparison:
                # [FASE 4E COMPARISON] Agregación multi-columna con TODAS las filas
                agged = (t.group_by(intent.dimension)
                         .aggregate(
                             valor_base=t[_original_plot_metric].sum().name('valor_base'),
                             valor_comparado=t[_dist_counterpart].sum().name('valor_comparado'),
                         )
                         .mutate(valor_variacion=ibis._.valor_comparado - ibis._.valor_base)
                         .order_by(ibis.desc('valor_variacion')))
                df_res = agged.to_pandas()
                
                if df_res.empty:
                    return {"type": "echarts", "chart_type": "bar", "data": [], "title": f"Sin datos para {intent.metric}", "error": "empty_result"}
                
                total_sample = df_res['valor_variacion'].sum()
                chart_data = []
                _dist_metric_human = _original_plot_metric.replace("_", " ").title()
                for _, row in df_res.iterrows():
                    clean_name = IbisEngine._format_chart_name(intent.dimension, row[intent.dimension])
                    chart_data.append({
                        "name": clean_name,
                        _dist_metric_human + f" ({_year_from_label})": float(row['valor_base']),
                        _dist_metric_human + f" ({_year_to_label})": float(row['valor_comparado']),
                        "Variación": float(row['valor_variacion']),
                    })
            else:
                # Flujo Estándar Uni-Dimensional
                agged = (t.group_by(intent.dimension)
                         .aggregate(valor=agg_expr, rank_val=rank_agg_expr)
                         .order_by(rank_order)
                         .limit(intent.limit))
                df_res = agged.to_pandas()
                
                if df_res.empty:
                    return {"type": "echarts", "chart_type": "bar", "data": [], "title": f"Sin datos para {intent.metric}", "error": "empty_result"}
                    
                total_sample = df_res['valor'].sum()
                df_res['_cum_pct'] = (df_res['valor'].cumsum() / (total_sample if total_sample else 1) * 100)
                
                chart_data = []
                for _, row in df_res.iterrows():
                    clean_name = IbisEngine._format_chart_name(intent.dimension, row[intent.dimension])
                    chart_data.append({
                        "name": clean_name, 
                        "value": float(row['valor']),
                        "extra_info": { "cumulative": f"{row['_cum_pct']:.1f}%" }
                    })
        else:
            # Flujo Multi-Dimensional (Top N Principal + Desglose)
            sec_dim = intent.group_by[0] # Tomamos la primera dimensión secundaria
            
            # PASO 1: Top N globales de la dimensión principal
            top_primary = (t.group_by(intent.dimension)
                           .aggregate(rank_val=rank_agg_expr)
                           .order_by(rank_order)
                           .limit(intent.limit))
            
            top_items_list = top_primary[intent.dimension].to_pandas().tolist()
            
            # 🛡️ Empty Data Guard
            if not top_items_list:
                return {"type": "echarts", "chart_type": "bar", "data": [], "title": f"Sin datos para {intent.metric}", "error": "empty_result"}
                
            # PASO 2: Filtrar tabla maestra y Agrupar por ambas
            t_filtered = t.filter(t[intent.dimension].isin(top_items_list))
            agged_multi = t_filtered.group_by([intent.dimension, sec_dim]).aggregate(valor=agg_expr)
            df_long = agged_multi.to_pandas()
            
            # PASO 3: Pivotar (Unstack) a formato ChartFactory ECharts
            df_pivot = df_long.pivot_table(
                index=intent.dimension, 
                columns=sec_dim, 
                values='valor', 
                aggfunc='sum'
            ).fillna(0).reset_index()
            df_pivot.columns = [str(column_name) for column_name in df_pivot.columns]
            
            # Ordenar pivot table usando el _total
            value_cols = [c for c in df_pivot.columns if c != str(intent.dimension)]
            df_pivot['_total'] = df_pivot[value_cols].sum(axis=1)
            df_pivot = df_pivot.sort_values('_total', ascending=False).drop(columns=['_total'])
            
            total_sample = df_long['valor'].sum()
            
            # Formatear a lista de diccionarios que ChartFactory entenderá nativamente como Multi-Serie
            chart_data = []
            for _, row in df_pivot.iterrows():
                row_dict = row.to_dict()
                # Clean name
                raw_name = row_dict.pop(str(intent.dimension), row_dict.pop(intent.dimension, "N/A"))
                row_dict["name"] = IbisEngine._format_chart_name(intent.dimension, raw_name)
                # Ensure values are float
                for k, v in row_dict.items():
                    if k != "name": row_dict[k] = float(v)
                chart_data.append(row_dict)

        # 3. Selección del Gráfico y Retorno
        chart_type = intent.visual_protocol.value if intent.visual_protocol else "bar"
        
        response = {
            "type": "echarts",
            "chart_type": chart_type,
            "data": chart_data,
            "title": f"Distribución de {plot_metric} por {intent.dimension}",
            "hard_facts": {
                "total_sample": float(total_sample) if total_sample else 0,
                "plot_metric": plot_metric,
                "ranking_metric": ranking_metric,
            }
        }

        if _dist_is_comparison and chart_data:
            _dist_suffix = _dist_counterpart[len(_original_plot_metric):]
            _dist_year_to = ""
            for _sep in ("_sheet__", "_related_"):
                if _sep in _dist_suffix:
                    _dist_year_to = _dist_suffix.split(_sep, 1)[-1]
                    break
            _dist_metric_human = _original_plot_metric.replace("_", " ").title()
            response["title"] = (
                f"Variación de {_dist_metric_human} por {intent.dimension}"
                + (f" ({_dist_year_to})" if _dist_year_to else "")
            )
            _positive = sum(1 for d in chart_data if d.get("Variación", 0) > 0)
            _negative = sum(1 for d in chart_data if d.get("Variación", 0) < 0)
            response["hard_facts"]["comparison"] = {
                "metric": _original_plot_metric,
                "metric_humanized": _dist_metric_human,
                "year_to": _dist_year_to,
                "total_variation": round(float(total_sample), 2) if total_sample else 0,
                "positive_changes": _positive,
                "negative_changes": _negative,
                "total_entities": len(chart_data),
            }
        
        # Inyectar barmode para que ChartFactory lo aplique si es necesario
        if has_secondary_dim:
            response["barmode"] = getattr(intent, 'barmode', 'stacked')
        elif _dist_is_comparison:
            response["barmode"] = "grouped"
        elif _dist_is_comparison:
            # ── ADR-VISUAL-002 ──────────────────────────────────────────
            # Comparison data without group_by still has multi-column
            # structure (Base, Comparado, Variación). Without barmode,
            # ChartFactory takes the uni-dimensional path and destroys
            # the comparison columns via _normalize_data_polymorphic.
            # "grouped" signals multi-series rendering (side-by-side bars).
            # ───────────────────────────────────────────────────────────
            response["barmode"] = "grouped"
            
        return response

    # =========================================================================
    # 🩺 DIAGNOSTIC ANALYSIS (Phase 1: "Why did it happen?")
    # Handles: Boxplot, Scatter, Funnel — schema-agnostic
    # =========================================================================
    @staticmethod
    def _analyze_diagnostic(t, intent: DiagnosticIntent, plan: AnalysisPlan | None = None):
        """
        Diagnostic intent handler. Routes to the appropriate statistical analysis
        based on the visual_protocol or data characteristics.
        Works with ANY column names — uses only the column references in the intent.
        """
        chart_data = []  # Defensive initialization
        visual_type = getattr(intent, 'visual_protocol', None)
        visual_str = visual_type.value if visual_type else 'boxplot'
        
        # Get metric and dimension from the intent
        metric_col = getattr(intent, 'metric', None) or getattr(intent, 'metrics', [None])[0]
        dimension_col = getattr(intent, 'dimension', None) or (getattr(intent, 'group_by', [None]) or [None])[0]
        
        if not metric_col:
            return {"error": "No se encontró columna métrica para análisis diagnóstico."}
        
        col_met = t[metric_col]
        
        # --- BOXPLOT ---
        if 'boxplot' in visual_str:
            if dimension_col:
                stats_df = IbisEngine._calculate_boxplot_stats(t, col_met, dimension_col)
                if stats_df is not None:
                    records = stats_df.to_dict(orient='records')
                    # 🕵️ [FIX D] Spy + Enrich: add explicit 'name' key for ChartFactory
                    print(f"🕵️ [BOXPLOT SPY] Raw records keys: {list(records[0].keys()) if records else 'EMPTY'}")
                    print(f"🕵️ [BOXPLOT SPY] First record: {records[0] if records else 'EMPTY'}")
                    for rec in records:
                        # Inject 'name' from dimension column so _get_smart_keys finds it
                        if 'name' not in rec and dimension_col in rec:
                            rec['name'] = str(rec[dimension_col])
                    
                    # 🔴 [PHASE 2] IQR Outlier Detection
                    # Rule: outlier if value < Q1 - 1.5*IQR OR value > Q3 + 1.5*IQR
                    outliers = []
                    try:
                        df_raw = t.select(metric_col, dimension_col).to_pandas()
                        for rec in records:
                            cat_name = rec.get('name', '')
                            q1 = float(rec.get('q1', 0))
                            q3 = float(rec.get('q3', 0))
                            iqr = q3 - q1
                            lower_fence = q1 - 1.5 * iqr
                            upper_fence = q3 + 1.5 * iqr
                            
                            cat_data = df_raw[df_raw[dimension_col].astype(str) == str(cat_name)][metric_col].dropna()
                            cat_outliers = cat_data[(cat_data < lower_fence) | (cat_data > upper_fence)]
                            
                            for val in cat_outliers.values[:20]:  # Max 20 outliers per category
                                outliers.append({"name": str(cat_name), "value": float(val)})
                        
                        if outliers:
                            print(f"🔴 [OUTLIERS] Detectados {len(outliers)} valores atípicos")
                    except Exception as e:
                        print(f"⚠️ [OUTLIERS] Error detectando: {e}")
                    
                    return {
                        "type": "echarts",
                        "chart_type": "boxplot",
                        "data": records,
                        "outliers": outliers,
                        "title": f"Variabilidad: {metric_col} por {dimension_col}",
                        "hard_facts": {
                            "analysis_type": "diagnostic_boxplot",
                            "metric": metric_col,
                            "dimension": dimension_col,
                            "total_outliers": len(outliers)
                        }
                    }
            # Boxplot without dimension — single column stats
            df_raw = t.select(metric_col).to_pandas()
            col_data = df_raw[metric_col].dropna()
            if not col_data.empty:
                stats = {
                    "min": float(col_data.min()),
                    "q1": float(col_data.quantile(0.25)),
                    "median": float(col_data.median()),
                    "q3": float(col_data.quantile(0.75)),
                    "max": float(col_data.max()),
                    "mean": float(col_data.mean()),
                    "std": float(col_data.std())
                }
                return {
                    "type": "echarts",
                    "chart_type": "boxplot",
                    "data": [{"name": metric_col, **stats}],
                    "title": f"Distribución Estadística: {metric_col}",
                    "hard_facts": stats
                }
        
        # --- SCATTER ---
        elif 'scatter' in visual_str:
            raw_metric_candidates = []
            for candidate in [
                getattr(intent, 'metric', None),
                getattr(intent, 'value_column', None),
                *list(getattr(intent, 'metrics', []) or []),
            ]:
                if candidate and candidate in t.columns and candidate not in raw_metric_candidates:
                    raw_metric_candidates.append(candidate)

            if len(raw_metric_candidates) >= 2:
                temporal_metrics = [
                    candidate for candidate in raw_metric_candidates
                    if IbisEngine._is_temporal_column(t, candidate)
                ]
                numeric_metrics = [
                    candidate for candidate in raw_metric_candidates
                    if IbisEngine._is_numeric_column(t, candidate)
                ]

                x_col = raw_metric_candidates[0]
                y_col = raw_metric_candidates[1]
                x_expr = t[x_col]
                y_expr = t[y_col]
                x_label = IbisEngine._normalize_metric_label(x_col)
                y_label = IbisEngine._normalize_metric_label(y_col)

                def _derive_temporal_axis(metric_name: str):
                    reference_col = IbisEngine._pick_reference_date_column(intent, t.columns, exclude=metric_name)
                    if not reference_col or not IbisEngine._is_temporal_column(t, reference_col):
                        return None, None, None

                    derived_expr = (t[metric_name].epoch_seconds() - t[reference_col].epoch_seconds()) / 86400
                    metric_name_lower = metric_name.lower()
                    if any(token in metric_name_lower for token in ['venc', 'caduc', 'expiry', 'expir', 'prefercons']):
                        derived_label = "Días a vencimiento"
                    else:
                        derived_label = f"Días desde {IbisEngine._normalize_metric_label(reference_col)}"

                    return derived_expr, derived_label, reference_col

                x_is_temporal = IbisEngine._is_temporal_column(t, x_col)
                y_is_temporal = IbisEngine._is_temporal_column(t, y_col)

                if len(temporal_metrics) >= 2:
                    expiry_col = next(
                        (
                            candidate for candidate in temporal_metrics
                            if any(token in candidate.lower() for token in ['venc', 'caduc', 'expiry', 'expir', 'prefercons'])
                        ),
                        temporal_metrics[0],
                    )
                    base_col = next(
                        (
                            candidate for candidate in temporal_metrics
                            if candidate != expiry_col and any(token in candidate.lower() for token in ['stock', 'fecha', 'date', 'period'])
                        ),
                        next((candidate for candidate in temporal_metrics if candidate != expiry_col), None),
                    )
                    y_metric_col = next((candidate for candidate in numeric_metrics if candidate != expiry_col and candidate != base_col), None)

                    if not base_col or not y_metric_col:
                        return {
                            "error": (
                                "Scatter temporal requiere dos fechas compatibles y una métrica numérica para Y. "
                                f"Métricas detectadas: {raw_metric_candidates}"
                            )
                        }

                    x_expr = (t[expiry_col].epoch_seconds() - t[base_col].epoch_seconds()) / 86400
                    y_expr = t[y_metric_col]
                    x_label = "Días a vencimiento"
                    y_label = IbisEngine._normalize_metric_label(y_metric_col)
                    x_col = expiry_col
                    y_col = y_metric_col
                    print(
                        f"🧠 [SCATTER DERIVED METRIC] '{expiry_col}' - '{base_col}' convertido a '{x_label}' "
                        f"con Y='{y_metric_col}'."
                    )
                elif x_is_temporal and not y_is_temporal:
                    derived_expr, derived_label, reference_col = _derive_temporal_axis(x_col)
                    if derived_expr is None:
                        return {
                            "error": (
                                f"Scatter temporal requiere una fecha de referencia para derivar '{x_col}'. "
                                "No se encontró una columna temporal base válida en el dataset."
                            )
                        }
                    x_expr = derived_expr
                    x_label = derived_label
                    print(
                        f"🧠 [SCATTER DERIVED METRIC] '{x_col}' convertido a '{x_label}' "
                        f"usando referencia '{reference_col}'."
                    )
                elif y_is_temporal and not x_is_temporal:
                    derived_expr, derived_label, reference_col = _derive_temporal_axis(y_col)
                    if derived_expr is None:
                        return {
                            "error": (
                                f"Scatter temporal requiere una fecha de referencia para derivar '{y_col}'. "
                                "No se encontró una columna temporal base válida en el dataset."
                            )
                        }
                    y_expr = derived_expr
                    y_label = derived_label
                    print(
                        f"🧠 [SCATTER DERIVED METRIC] '{y_col}' convertido a '{y_label}' "
                        f"usando referencia '{reference_col}'."
                    )

                if dimension_col:
                    scatter_df = (
                        t.group_by(dimension_col)
                        .aggregate(
                            x_value=x_expr.mean(),
                            y_value=y_expr.mean(),
                        )
                        .order_by(ibis.desc('y_value'))
                        .limit(500)
                        .to_pandas()
                    )
                    scatter_df = scatter_df.dropna(subset=['x_value', 'y_value'])
                    scatter_data = [
                        {
                            "name": IbisEngine._format_chart_name(dimension_col, row[dimension_col]),
                            "raw_name": IbisEngine._format_chart_name(dimension_col, row[dimension_col]),
                            "series": IbisEngine._format_chart_name(dimension_col, row[dimension_col]),
                            "x_value": float(row['x_value']),
                            "y_value": float(row['y_value']),
                        }
                        for _, row in scatter_df.iterrows()
                    ]
                    corr_source = scatter_df[['x_value', 'y_value']]
                else:
                    df_scatter = (
                        t.select(
                            x_expr.name('x_value'),
                            y_expr.name('y_value'),
                        )
                        .limit(500)
                        .to_pandas()
                    )
                    df_scatter = df_scatter.dropna(subset=['x_value', 'y_value'])
                    scatter_data = [
                        {
                            "name": f"Punto {idx + 1}",
                            "raw_name": f"Punto {idx + 1}",
                            "x_value": float(row['x_value']),
                            "y_value": float(row['y_value']),
                        }
                        for idx, (_, row) in enumerate(df_scatter.iterrows())
                    ]
                    corr_source = df_scatter[['x_value', 'y_value']]
                
                # Calculate correlation
                if len(corr_source) > 2:
                    corr = corr_source.iloc[:, 0].corr(corr_source.iloc[:, 1])
                else:
                    corr = 0.0
                
                return {
                    "type": "echarts",
                    "chart_type": "scatter",
                    "data": scatter_data,
                    "x_axis": x_label,
                    "y_axis": y_label,
                    "series_label": IbisEngine._normalize_metric_label(dimension_col) if dimension_col else None,
                    "title": f"Correlación: {x_label} vs {y_label}",
                    "hard_facts": {
                        "correlation": round(corr, 3),
                        "strength": "Fuerte" if abs(corr) > 0.7 else "Moderada" if abs(corr) > 0.4 else "Débil",
                        "sample_size": len(scatter_data)
                    }
                }
        
        # --- FUNNEL ---
        elif 'funnel' in visual_str:
            if dimension_col:
                funnel_limit = min(getattr(intent, 'limit', 10) or 10, 15)
                funnel_df = IbisEngine._analyze_funnel_conversion(
                    t,
                    col_met,
                    dimension_col,
                    limit=funnel_limit,
                )
                if funnel_df is not None:
                    chart_data = [
                        {"name": str(row[dimension_col]), "value": float(row['valor']),
                         "extra_info": {"conversion": f"{row['conversion_rate']}%"}}
                        for _, row in funnel_df.iterrows()
                    ]
                    return {
                        "type": "echarts",
                        "chart_type": "funnel",
                        "data": chart_data,
                        "title": f"Embudo: {metric_col} por {dimension_col}",
                        "hard_facts": {
                            "stages": len(funnel_df),
                            "top_stage": str(funnel_df.iloc[0][dimension_col]) if not funnel_df.empty else "N/A"
                        }
                    }
        
        # Fallback: gráfico agregado seguro para no perder el tercer visual
        print(f"⚠️ [IBIS][DIAGNOSTIC] Fallback agregado activado para protocolo '{visual_str}'")

        # [FASE 4E COMPARISON ENGINE] Detectar columnas de JOIN y calcular variación
        _counterpart = None
        for _col in t.columns:
            if (_col.startswith(f"{metric_col}_sheet__")
                    or _col.startswith(f"{metric_col}_related_")):
                _counterpart = _col
                break
        _original_metric_col = metric_col
        if _counterpart:
            print(f"📊 [IBIS][COMPARISON] Variación detectada: {_counterpart} - {metric_col}")
            t = t.mutate(_variation=t[_counterpart] - t[metric_col])
            metric_col = "_variation"
            col_met = t[metric_col]

        # === COMPARISON BRANCH: Todos los datos + multi-serie + narrativa enriquecida ===
        if dimension_col and _counterpart:
            _suffix = _counterpart[len(_original_metric_col):]
            _year_to_label = ""
            for _sep in ("_sheet__", "_related_"):
                if _sep in _suffix:
                    _year_to_label = _suffix.split(_sep, 1)[-1]
                    break
            # Extract year from plan's primary_frame_id when available
            _year_from_label = "Base"
            if plan and hasattr(plan, 'primary_frame_id'):
                _pf = plan.primary_frame_id or ""
                _pf_parts = _pf.split("::")
                _from = _pf_parts[-1] if len(_pf_parts) > 1 else _pf
                if _from and _from != "primary":
                    _year_from_label = _from

            _compare_df = (
                t.select(dimension_col, _original_metric_col, _counterpart, "_variation")
                .order_by(ibis.desc("_variation"))
                .to_pandas()
            )

            if not _compare_df.empty:
                _label_from = _year_from_label or _original_metric_col
                _label_to = _year_to_label or _counterpart
                chart_data = []
                _positive = 0
                _negative = 0
                _total_var = 0.0
                for _, row in _compare_df.iterrows():
                    clean_name = IbisEngine._format_chart_name(dimension_col, row[dimension_col])
                    _vf = float(row[_original_metric_col])
                    _vt = float(row[_counterpart])
                    _vv = float(row["_variation"])
                    if _vv > 0:
                        _positive += 1
                    elif _vv < 0:
                        _negative += 1
                    _total_var += _vv
                    chart_data.append({
                        "name": clean_name,
                        _label_from: _vf,
                        _label_to: _vt,
                        "Variación": _vv,
                    })

                _metric_human = IbisEngine._normalize_metric_label(_original_metric_col)
                _dim_human = IbisEngine._normalize_metric_label(dimension_col) if not dimension_col.startswith("_") else dimension_col
                return {
                    "type": "echarts",
                    "chart_type": "bar",
                    "data": chart_data,
                    "barmode": "grouped",
                    "_ibis_fallback": True,
                    "title": f"Variación de {_metric_human} por {_dim_human} ({_label_from} vs {_label_to})",
                    "hard_facts": {
                        "analysis_type": "comparison_variation",
                        "metric": _original_metric_col,
                        "metric_humanized": _metric_human,
                        "dimension": dimension_col,
                        "comparison": {
                            "year_from": _label_from,
                            "year_to": _label_to,
                            "metric": _original_metric_col,
                            "total_variation": round(_total_var, 2),
                            "positive_changes": _positive,
                            "negative_changes": _negative,
                            "total_entities": len(chart_data),
                        }
                    }
                }
        # === END COMPARISON BRANCH ===

        aggregation = getattr(intent, 'aggregation', 'sum')
        if aggregation == "avg":
            agg_expr = col_met.mean().name('valor')
        elif aggregation == "count":
            agg_expr = col_met.count().name('valor')
        elif aggregation == "max":
            agg_expr = col_met.max().name('valor')
        elif aggregation == "min":
            agg_expr = col_met.min().name('valor')
        else:
            agg_expr = col_met.sum().name('valor')

        if dimension_col:
            fallback_df = (
                t.group_by(dimension_col)
                .aggregate(agg_expr)
                .order_by(ibis.desc('valor'))
                .limit(10)
                .to_pandas()
            )

            if not fallback_df.empty:
                chart_data = []
                total_val = float(fallback_df['valor'].sum()) if 'valor' in fallback_df.columns else 0.0
                for _, row in fallback_df.iterrows():
                    clean_name = IbisEngine._format_chart_name(dimension_col, row[dimension_col])
                    share = (float(row['valor']) / total_val * 100) if total_val else 0.0
                    chart_data.append({
                        "name": clean_name,
                        "value": float(row['valor']),
                        "extra_info": {"share": f"{share:.1f}%"}
                    })

                return {
                    "type": "echarts",
                    "chart_type": "bar",
                    "data": chart_data,
                    "title": f"Diagnóstico de {metric_col} por {dimension_col}",
                    "hard_facts": {
                        "analysis_type": "diagnostic_fallback_bar",
                        "metric": metric_col,
                        "dimension": dimension_col,
                        "top_1_name": str(fallback_df.iloc[0][dimension_col]),
                        "top_1_val": float(fallback_df.iloc[0]['valor']),
                        "total_analyzed": total_val,
                    }
                }

        # [FASE 4E COMPARISON] KPI comparativo: total neto de variación entre períodos
        if _counterpart and not dimension_col:
            _total_var = float(t['_variation'].sum().execute())
            _total_base = float(t[_original_metric_col].sum().execute())
            _total_compared = float(t[_counterpart].sum().execute())
            _var_pct = ((_total_compared - _total_base) / _total_base * 100) if abs(_total_base) > 0.001 else 0.0
            _metric_human = IbisEngine._normalize_metric_label(_original_metric_col)
            _suffix = _counterpart[len(_original_metric_col):]
            _kpi_year_to = ""
            for _sep in ("_sheet__", "_related_"):
                if _sep in _suffix:
                    _kpi_year_to = _suffix.split(_sep, 1)[-1]
                    break
            _kpi_year_from = "Base"
            if plan and hasattr(plan, 'primary_frame_id'):
                _pf = plan.primary_frame_id or ""
                _pf_parts = _pf.split("::")
                _from = _pf_parts[-1] if len(_pf_parts) > 1 else _pf
                if _from and _from != "primary":
                    _kpi_year_from = _from
            return {
                "type": "kpi",
                "chart_type": "kpi",
                "data": {
                    f"{_metric_human} ({_kpi_year_from})": round(_total_base, 2),
                    f"{_metric_human} ({_kpi_year_to})": round(_total_compared, 2),
                    "Variación": round(_total_var, 2),
                    "%": round(_var_pct, 1),
                },
                "barmode": "grouped",
                "_ibis_fallback": True,
                "title": f"Variación Total de {_metric_human} ({_kpi_year_from} vs {_kpi_year_to})",
                "hard_facts": {
                    "comparison": {
                        "year_from": _kpi_year_from,
                        "year_to": _kpi_year_to,
                        "metric": _original_metric_col,
                        "metric_humanized": _metric_human,
                        "total_variation": round(_total_var, 2),
                        "base_total": round(_total_base, 2),
                        "compared_total": round(_total_compared, 2),
                        "variation_pct": round(_var_pct, 1),
                    }
                }
            }

        summary_df = t.aggregate(agg_expr).to_pandas()
        if not summary_df.empty and 'valor' in summary_df.columns:
            return {
                "type": "kpi",
                "chart_type": "kpi",
                "data": {metric_col: float(summary_df.iloc[0]['valor'])}
            }

        return {"error": "No se pudo construir un análisis diagnóstico válido."}

    # =========================================================================
    # 🔮 PREDICTIVE ANALYSIS (Phase 2: "What will happen?")
    # Delegates to PredictiveEngine for forecasting and anomalies
    # =========================================================================
    @staticmethod
    def _analyze_predictive(t, intent):
        """
        Predictive intent handler. Converts Ibis table to Pandas and delegates
        to PredictiveEngine for forecasting or anomaly detection.
        Schema-agnostic: uses column references from the intent.
        """
        try:
            from app.services.predictive_engine import PredictiveEngine
        except ImportError:
            return {"error": "PredictiveEngine not available."}
        
        # Extract column references from intent
        date_col = getattr(intent, 'date_column', None)
        value_col = getattr(intent, 'value_column', None) or getattr(intent, 'metric', None)
        analysis_type = getattr(intent, 'analysis_subtype', 'forecast')
        
        if not date_col or not value_col:
            # Try to auto-detect from the table schema
            df_full = t.to_pandas()
            date_candidates = [c for c in df_full.columns if pd.api.types.is_datetime64_any_dtype(df_full[c])]
            num_candidates = [c for c in df_full.columns if pd.api.types.is_numeric_dtype(df_full[c])]
            
            if not date_col and date_candidates:
                date_col = date_candidates[0]
            if not value_col and num_candidates:
                value_col = num_candidates[0]
        
        if not date_col or not value_col:
            return {"error": "No se encontraron columnas de fecha y valor para predicción."}
        
        df = t.to_pandas()
        
        # --- FORECASTING ---
        if analysis_type in ['forecast', 'trend_projection']:
            raw_forecast = PredictiveEngine.forecast_series(df, date_col, value_col)
            
            if raw_forecast:
                chart_data = [
                    {
                        "name": item['date'],
                        "value": item['value'],
                        "extra_info": {
                            "type": item['type'],
                            "lower_ci": item.get('lower_ci'),
                            "upper_ci": item.get('upper_ci')
                        }
                    }
                    for item in raw_forecast
                ]
                
                return {
                    "type": "echarts",
                    "chart_type": "line_chart",
                    "data": chart_data,
                    "title": f"Proyección: {value_col}",
                    "hard_facts": {
                        "total_points": len(chart_data),
                        "forecast_points": sum(1 for d in chart_data if d['extra_info'].get('type') == 'forecast'),
                        "metric": value_col
                    }
                }
            return {"error": "Datos insuficientes para generar proyección."}
        
        # --- ANOMALY DETECTION ---
        elif analysis_type == 'anomalies':
            anomaly_result = PredictiveEngine.detect_anomalies(df, value_col)
            
            if anomaly_result is not None and not anomaly_result.empty:
                # Return top anomalies as scatter overlay
                top_anomalies = anomaly_result.head(50)
                chart_data = [
                    {"name": str(row.get(date_col, idx)), "value": float(row[value_col]),
                     "extra_info": {"is_anomaly": True, "score": float(row.get('anomaly_score', 0))}}
                    for idx, row in top_anomalies.iterrows()
                ]
                return {
                    "type": "echarts",
                    "chart_type": "scatter",
                    "data": chart_data,
                    "title": f"Anomalías Detectadas: {value_col}",
                    "hard_facts": {
                        "total_anomalies": len(anomaly_result),
                        "shown": len(chart_data)
                    }
                }
            return {"error": "No se detectaron anomalías significativas."}
        
        return {"error": f"Subtipo predictivo '{analysis_type}' no reconocido."}
