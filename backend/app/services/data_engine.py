import csv
from contextlib import contextmanager
import json
import pandas as pd
import numpy as np
import io
import re
import os
import threading
import unicodedata

try:
    import fcntl
except ImportError:  # pragma: no cover - Cloud Run y macOS disponen de fcntl.
    fcntl = None

from pydantic import BaseModel

from app.core.analytical_contract import (
    AnalyticalContract,
    compute_bootstrap_expiry,
    is_bootstrap_expired,
)
from app.core.config import settings
from app.core.structured_logging import emit_structured_log
from app.core.temporal_axis import detect_ordinal_axis, series_monotonic_direction

class DataEngine:
    """
    Motor de Ingeniería de Datos V3 (Unicorn Edition).
    Responsabilidades: Lectura Robusta, Limpieza Inteligente, Unificación y Topología.
    """

    _SEMANTIC_CONTRACT_VERSION = "phase_1_foundation_v3"
    _sidecar_refresh_locks: dict[str, threading.Lock] = {}
    _sidecar_refresh_locks_guard = threading.Lock()

    @classmethod
    def semantic_contract_version(cls) -> str:
        return cls._SEMANTIC_CONTRACT_VERSION

    @staticmethod
    def read_file(file_bytes: bytes, filename: str) -> dict:
        """Lector Universal Blindado V2. Dtype=String y Sniffer de Separadores."""
        dfs = {}
        filename = filename.lower()
        f_io = io.BytesIO(file_bytes)

        try:
            # A. ESTRATEGIA EXCEL (Forzamos dtype=str para no perder ceros iniciales)
            if filename.endswith(('.xlsx', '.xls')):
                try:
                    xls = pd.ExcelFile(f_io)
                    for sheet in xls.sheet_names:
                        # LEER TODO COMO TEXTO INICIALMENTE
                        dfs[sheet] = pd.read_excel(xls, sheet_name=sheet, header=0, dtype=str)
                except Exception as e:
                    print(f"Warn: Excel read failed, trying CSV fallback. {e}")
                    f_io.seek(0)
            
            # B. ESTRATEGIA CSV (Sniffer + Dtype String)
            if not dfs:
                encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
                for enc in encodings:
                    try:
                        f_io.seek(0)
                        # 1. Detectar Separador (Sniffer)
                        sample = f_io.read(2048).decode(enc)
                        f_io.seek(0)
                        dialect = csv.Sniffer().sniff(sample, delimiters=',;|')
                        delimiter = dialect.delimiter
                        
                        # 2. Leer con separador detectado y TODO COMO TEXTO
                        df = pd.read_csv(f_io, encoding=enc, sep=delimiter, dtype=str, on_bad_lines='skip')
                        
                        if len(df.columns) > 1:
                            dfs['principal'] = df
                            print(f"Success CSV: Enc={enc}, Sep='{delimiter}'")
                            break
                    except Exception as _enc_err:
                        print(f"Warn: CSV enc={enc} falló ({type(_enc_err).__name__})")
                        continue
                        
            if not dfs: raise ValueError("Formato de archivo desconocido o ilegible.")
            return dfs

        except Exception as e:
            print(f"CRITICAL DATA ENGINE ERROR: {str(e)}")
            return {}

    @staticmethod
    def _semantic_tokens(value: str) -> list[str]:
        normalized = unicodedata.normalize('NFKD', str(value or ''))
        normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.lower()
        normalized = re.sub(r'[^a-z0-9]+', ' ', normalized)
        return [token for token in normalized.split() if token]

    @staticmethod
    def _has_identifier_semantic_name(column_name: str) -> bool:
        tokens = set(DataEngine._semantic_tokens(column_name))
        if not tokens:
            return False

        identifier_tokens = {
            'id', 'sku', 'material', 'lote', 'codigo', 'cod', 'documento',
            'dni', 'ruc', 'serie', 'folio', 'voucher', 'ticket'
        }
        if tokens & identifier_tokens:
            return True

        normalized = '_'.join(tokens)
        return normalized == 'id' or normalized.startswith('id_') or normalized.endswith('_id')

    @staticmethod
    def _has_measure_semantic_name(column_name: str) -> bool:
        tokens = set(DataEngine._semantic_tokens(column_name))
        if not tokens:
            return False

        measure_tokens = {
            'cantidad', 'cantidades', 'venta', 'ventas', 'total', 'importe', 'monto',
            'ingreso', 'ingresos', 'revenue', 'facturacion', 'precio', 'price',
            'costo', 'coste', 'cost', 'stock', 'saldo', 'saldos', 'qty',
            'quantity', 'units', 'unidades', 'volumen', 'pieza', 'piezas',
            'porcentaje', 'ratio', 'margen', 'growth', 'variacion', 'pen', 'usd', 'eur',
            'multa', 'multas', 'penalidad', 'penalidades',
            # [DUAL-ROLE 2026-09] Magnitudes que pueden tener baja cardinalidad
            # (ej: desperfectos_mecanicos = 0/1/2) y NO deben clasificarse como
            # dimensión. Se incluyen formas singular y plural. NO agregar
            # adjetivos/sustantivos de persona (ej: 'mecanicos'), que forzarían
            # columnas tipo tipo_mecanico o id_mecanico a clasificarse como métrica.
            'desperfecto', 'desperfectos', 'falla', 'fallas',
            'averia', 'averias', 'incidente', 'incidentes',
            'retraso', 'retrasos', 'incumplimiento', 'incumplimientos',
            'observacion', 'observaciones',
        }
        return bool(tokens & measure_tokens)

    @staticmethod
    def _looks_like_identifier_numeric_series(sample: pd.Series) -> bool:
        sample = sample.dropna().astype(str).str.strip()
        sample = sample[sample != '']
        if sample.empty:
            return False

        compact = sample.str.replace(r'\s+', '', regex=True)
        decimal_ratio = compact.str.contains(r'[.,]\d+$', regex=True).mean()
        if decimal_ratio > 0.2:
            return False

        alpha_numeric_ratio = compact.str.contains(r'[A-Za-z]', regex=True).mean()
        integer_like_ratio = compact.str.fullmatch(r'[-+]?\d+', na=False).mean()
        leading_zero_ratio = compact.str.fullmatch(r'0\d+', na=False).mean()
        lengths = compact.str.replace(r'^[-+]', '', regex=True).str.len()
        fixed_width = lengths.nunique() <= 2

        numeric_values = pd.to_numeric(compact.str.replace(',', '.', regex=False), errors='coerce').dropna()
        sequential_ratio = 0.0
        if integer_like_ratio > 0.9 and len(numeric_values) >= 5:
            sorted_unique = np.sort(numeric_values.astype(float).unique())
            if len(sorted_unique) >= 5:
                diffs = np.diff(sorted_unique)
                if len(diffs) > 0:
                    sequential_ratio = float(np.isclose(diffs, 1).mean())

        return bool(
            alpha_numeric_ratio > 0.15
            or leading_zero_ratio > 0.1
            or (integer_like_ratio > 0.95 and fixed_width)
            or sequential_ratio >= 0.8
        )

    @staticmethod
    def _should_force_identifier_from_uniqueness(
        column_name: str,
        sample: pd.Series,
        cardinality: int,
        cardinality_ratio: float,
    ) -> bool:
        if not (cardinality_ratio > 0.9 and cardinality > 20):
            return False

        if DataEngine._has_measure_semantic_name(column_name):
            return False

        if DataEngine._has_identifier_semantic_name(column_name):
            return True

        return DataEngine._looks_like_identifier_numeric_series(sample)

    # --- 🧠 NUEVOS MOTORES V6.1 (PEGAR DENTRO DE LA CLASE DataEngine) ---

    @staticmethod
    def _smart_merge_sheets(dfs: dict) -> pd.DataFrame:
        """
        Lógica Big Data: Decide si hace UNION (Tiempo) o JOIN (Relacional).
        """
        if not dfs: return pd.DataFrame()
        if len(dfs) == 1: return list(dfs.values())[0]

        sheet_names = list(dfs.keys())
        first_df = dfs[sheet_names[0]]
        
        # 1. DETECCIÓN DE SERIE DE TIEMPO (Tus Inventarios)
        # Si las pestañas parecen fechas (ej: 31-05-2021, Enero, Q1)
        date_pattern = re.compile(r'(\d{2,4}[-./]\d{2}[-./]\d{2,4})|(\d{2}[-./]\d{2}[-./]\d{4})|ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic', re.IGNORECASE)
        is_time_series = all(bool(date_pattern.search(str(name))) for name in sheet_names)

        if is_time_series:
            print(">>> [DATA ENGINE] Modo detectado: UNION ALL (Serie de Tiempo)")
            unified = []
            for name, df in dfs.items():
                # Inyectamos la "Llave Temporal Virtual"
                df['_virtual_snapshot_date_'] = name 
                unified.append(df)
            return pd.concat(unified, ignore_index=True)

        # 2. DETECCIÓN RELACIONAL (Personas + Ciudades)
        # Si comparten una ID clave (DNI, SKU, CODIGO)
        common_cols = set(first_df.columns)
        for df in dfs.values():
            common_cols.intersection_update(set(df.columns))
        
        # Buscamos si la intersección tiene "cara de ID"
        key_candidates = [c for c in common_cols if any(x in str(c).lower() for x in ['id', 'cod', 'sku', 'dni', 'ruc'])]
        
        if key_candidates:
            join_key = key_candidates[0]
            print(f">>> [DATA ENGINE] Modo detectado: LEFT JOIN (Relacional) usando clave '{join_key}'")
            base_df = first_df
            
            # Left Join iterativo
            for i in range(1, len(sheet_names)):
                next_df = dfs[sheet_names[i]]
                if join_key in next_df.columns:
                    base_df = pd.merge(base_df, next_df, on=join_key, how='left', suffixes=('', f'_{sheet_names[i]}'))
            
            return base_df

        # 3. FALLBACK: Tomamos la primera hoja (La más importante)
        print(">>> [DATA ENGINE] Modo detectado: SINGLE SHEET (Fallback)")
        return first_df

    @staticmethod
    def _detect_currency(df: pd.DataFrame) -> dict:
        """
        Detector de Moneda Nivel 1 (Headers) + Nivel 2 (Data Sampling).
        Evita alucinaciones del LLM imponiendo la realidad del archivo.
        """
        currency_map = {
            'PEN': r'\b(PEN|S/|SOLES|NUEVOS SOLES)\b',
            'USD': r'\b(USD|\$|DOLLAR|DOLARES)\b',
            'EUR': r'\b(EUR|€|EURO|EUROS)\b',
            'MXN': r'\b(MXN|PESOS)\b'
        }
        
        # Nivel 1: Headers (Prioridad Alta)
        for col in df.columns:
            for code, pattern in currency_map.items():
                if re.search(pattern, str(col), re.IGNORECASE):
                    symbol = "S/" if code == 'PEN' else "$" if code == 'USD' else "€" if code == 'EUR' else "$"
                    print(f"💰 [DATA ENGINE] Moneda detectada en Header '{col}': {code}")
                    return {'symbol': symbol, 'code': code}

        # Nivel 2: Data Sampling (Prioridad Media)
        # Buscamos en las primeras 5 filas de columnas tipo objeto o string
        sample_df = df.head(5)
        obj_cols = sample_df.select_dtypes(include=['object', 'string']).columns
        
        for col in obj_cols:
            for val in sample_df[col].dropna().astype(str):
                for code, pattern in currency_map.items():
                    # Buscamos el símbolo aislado o pegado a números
                    if re.search(pattern, val, re.IGNORECASE):
                        symbol = "S/" if code == 'PEN' else "$" if code == 'USD' else "€" if code == 'EUR' else "$"
                        print(f"💰 [DATA ENGINE] Moneda detectada en Data '{val}': {code}")
                        return {'symbol': symbol, 'code': code}
        
        return {'symbol': None, 'code': None}

    @staticmethod
    def _apply_entropy_sanitization(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        """
        Vacuna contra el "Código 130".
        Calcula la varianza. Si una columna numérica es casi constante, la degrada a Texto.
        """
        topology_overrides = {}
        for col in df.select_dtypes(include=[np.number]).columns:
            # No tocar si ya es flotante (dinero suele ser float, códigos suelen ser int)
            if pd.api.types.is_float_dtype(df[col]):
                continue

            # Cálculo de Entropía Simplificado (Unicidad)
            unique_count = df[col].nunique()
            total_count = len(df)
            
            # REGLA: Si el 90% de las filas tienen el mismo valor (ej: todo es '130')
            # O si hay muy pocos valores únicos (ej: solo 1, 2 o 3 valores en 10,000 filas)
            if unique_count < 5 and total_count > 100:
                print(f"🛡️ [ENTROPIA] Columna '{col}' degradada a DIMENSIÓN (Parece un ID o Código de Almacén).")
                df[col] = df[col].astype(str) # Forzamos a texto para que Ibis NO SUME
                topology_overrides[col] = "DIMENSION (Locked)"
        
        return df, topology_overrides

    # =========================================================================
    # 🧠 UNIVERSAL SCHEMA DISCOVERY (Schema-Agnostic Engine V7)
    # Rule: Inspect DATA CONTENT, never column NAMES.
    # =========================================================================
    @staticmethod
    def _classify_columns(df: pd.DataFrame) -> dict:
        """
        Universal Column Classifier — the core of Schema-Agnostic analysis.
        Inspects DATA CONTENT (cardinality, parseability, uniqueness), NOT column names.
        
        Returns: {
            col_name: {
                'type': 'numeric' | 'categorical' | 'temporal' | 'id',
                'role': 'metric' | 'dimension' | 'date' | 'identifier',
                'cardinality': int,
                'cardinality_ratio': float  # unique/total ratio
            }
        }
        """
        schema = {}
        total_rows = max(len(df), 1)  # Avoid division by zero
        
        for col in df.columns:
            # Skip internal columns
            if col.startswith('_') or col == 'is_latest_snapshot':
                continue
                
            info = {'type': 'unknown', 'role': 'unknown'}
            cardinality = df[col].nunique()
            cardinality_ratio = cardinality / total_rows
            col_lower = str(col).lower()
            is_descriptive_label = any(token in col_lower for token in ['texto', 'descripcion', 'descrip', 'nombre', 'breve'])
            is_semantic_dimension = (
                'tipo_almacen' in col_lower or
                ('tipo' in col_lower and 'almacen' in col_lower) or
                any(token in col_lower for token in ['ubicacion', 'location', 'warehouse'])
            )
            is_semantic_identifier = (
                not is_descriptive_label and
                DataEngine._has_identifier_semantic_name(col_lower)
            )

            # Semantic names from the business domain override raw parseability.
            if is_semantic_dimension:
                info = {'type': 'categorical', 'role': 'dimension'}
                info['cardinality'] = cardinality
                info['cardinality_ratio'] = round(cardinality_ratio, 3)
                schema[col] = info
                continue

            if is_semantic_identifier:
                info = {'type': 'id', 'role': 'identifier'}
                info['cardinality'] = cardinality
                info['cardinality_ratio'] = round(cardinality_ratio, 3)
                schema[col] = info
                continue
            
            # === PASS 1: Already typed by Pandas ===
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                info = {'type': 'temporal', 'role': 'date'}
            
            elif pd.api.types.is_numeric_dtype(df[col]):
                # Is it a real metric or a disguised ID?
                if pd.api.types.is_integer_dtype(df[col]):
                    # Integer with very high uniqueness → likely an ID
                    if DataEngine._should_force_identifier_from_uniqueness(
                        col,
                        df[col].dropna().astype(str),
                        cardinality,
                        cardinality_ratio,
                    ):
                        info = {'type': 'id', 'role': 'identifier'}
                    # Integer with low uniqueness AND low ratio → likely a code/category
                    # Example: tipo_almacen (6 values in 2000+ rows = 0.3% ratio)
                    # Counter-example: quantity_sold (19 values in 100 rows = 19% ratio) = metric
                    elif cardinality <= 20 and total_rows > 50 and cardinality_ratio < 0.1:
                        # [DUAL-ROLE 2026-09] Alineado con canonical_schema_profiler:
                        # si el nombre tiene semántica de medida (ej: desperfectos),
                        # es métrica aunque tenga baja cardinalidad. Sin este check,
                        # este segundo path anulaba la lógica del profiler.
                        if DataEngine._has_measure_semantic_name(col):
                            info = {'type': 'numeric', 'role': 'metric'}
                        else:
                            info = {'type': 'categorical', 'role': 'dimension'}
                    else:
                        info = {'type': 'numeric', 'role': 'metric'}
                else:
                    # Float → almost always a metric
                    info = {'type': 'numeric', 'role': 'metric'}
            
            # === PASS 2: Object/String — Try to infer ===
            else:
                sample = df[col].dropna().astype(str).str.strip()
                sample = sample[sample != '']  # Drop empty strings
                sample_size = max(len(sample), 1)
                
                # A. Try date parse first
                date_parsed = pd.to_datetime(sample, errors='coerce')
                date_ratio = date_parsed.notna().sum() / sample_size
                
                if date_ratio > 0.6:
                    # 🛡️ MIXED CONTENT GUARD (mirrors TYPE INFERENCE)
                    # If >5% of values fail to parse, it's mixed content → categorical
                    unparseable_ratio = 1 - date_ratio
                    has_alpha = sample.str.contains(r'[a-zA-Z]', regex=True).sum() / sample_size
                    
                    if unparseable_ratio > 0.05:
                        # Mixed content (e.g., "SALDOS" + "010731") → categorical
                        info = {'type': 'categorical', 'role': 'dimension'}
                    elif has_alpha < 0.1 and cardinality_ratio > 0.5 and cardinality > 100:
                        # Pure numeric codes with high cardinality → categorical
                        info = {'type': 'categorical', 'role': 'dimension'}
                    else:
                        info = {'type': 'temporal', 'role': 'date'}
                else:
                    # 🛡️ TEXT GUARD V8 (mirrors TYPE INFERENCE guard)
                    # If >30% of samples contain letter sequences, it's text, not numeric.
                    has_words = sample.str.contains(r'[a-zA-ZáéíóúñÑÁÉÍÓÚ]{2,}', regex=True)
                    word_ratio = has_words.sum() / sample_size
                    
                    if word_ratio > 0.3:
                        # Contains words → it's descriptive text
                        if cardinality_ratio > 0.9 and cardinality > 50:
                            info = {'type': 'id', 'role': 'identifier'}
                        else:
                            info = {'type': 'categorical', 'role': 'dimension'}
                    else:
                        # B. Try numeric parse (only for non-text columns)
                        clean_nums = sample.str.replace(r'[^\d.,-]', '', regex=True).str.replace(',', '.', regex=False)
                        nums = pd.to_numeric(clean_nums, errors='coerce')
                        num_ratio = nums.notna().sum() / sample_size
                        
                        if num_ratio > 0.8:
                            # Numeric content, but is it an ID?
                            if DataEngine._should_force_identifier_from_uniqueness(
                                col,
                                sample,
                                cardinality,
                                cardinality_ratio,
                            ):
                                info = {'type': 'id', 'role': 'identifier'}
                            elif cardinality <= 20 and total_rows > 50 and cardinality_ratio < 0.1:
                                info = {'type': 'categorical', 'role': 'dimension'}
                            else:
                                info = {'type': 'numeric', 'role': 'metric'}
                        else:
                            # C. It's categorical
                            if cardinality_ratio > 0.9 and cardinality > 50:
                                info = {'type': 'id', 'role': 'identifier'}
                            else:
                                info = {'type': 'categorical', 'role': 'dimension'}
            
            info['cardinality'] = cardinality
            info['cardinality_ratio'] = round(cardinality_ratio, 3)
            schema[col] = info
        
        # Log the schema profile
        print("\n" + "🧬" * 20)
        print("🧬 [SCHEMA DISCOVERY] Universal Column Classification:")
        for col, info in schema.items():
            emoji = {'numeric': '📊', 'categorical': '🏷️', 'temporal': '📅', 'id': '🔑'}.get(info['type'], '❓')
            print(f"   {emoji} {col}: {info['type']} → {info['role']} (cardinality: {info['cardinality']})")
        print("🧬" * 20 + "\n")
        
        return schema

    @staticmethod
    def unify_and_clean(dfs: dict, glossary_map: dict):
        
        # 1. Fusión Inteligente (V6.1)
        main_df = DataEngine._smart_merge_sheets(dfs)
        
        # [NUEVO] Detección de Moneda (Antes de limpiar columnas)
        currency_meta = DataEngine._detect_currency(main_df)
        
        # 2. Sanitización por Entropía (V6.1)
        main_df, entropy_notes = DataEngine._apply_entropy_sanitization(main_df)
        
        # 3. Limpieza Estándar
        if main_df.empty: return pd.DataFrame(), {}, "No Data", {}, {}

        cleaning_notes = []
        if entropy_notes:
            cleaning_notes.append(f"Sanitización Entrópica: {len(entropy_notes)} columnas degradadas a Texto.")
        
        # 2. NORMALIZACIÓN DE COLUMNAS (Accent/Symbol removal — universal)
        main_df.columns = [
            str(c).strip().lower()
            .replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
            .replace('/', '_').replace('-', '_').replace('.', '').replace(' ', '_')
            .replace('(', '').replace(')', '').replace('$', '').replace('%', '')
            for c in main_df.columns
        ]

        # =====================================================================
        # SCHEMA-AGNOSTIC RENAMING V7: Only Glossary, not hardcoded maps
        # The SemanticTranslator (Gemini) handles user-friendly aliases.
        # We only apply the USER'S team glossary here.
        # =====================================================================
        if glossary_map:
            clean_glossary = {str(k).lower().strip(): str(v).lower().strip() for k, v in glossary_map.items()}
            new_cols = {}
            for col in main_df.columns:
                col_lower = str(col).lower().strip()
                if col_lower in clean_glossary:
                    new_cols[col] = clean_glossary[col_lower]
            if new_cols:
                main_df = main_df.rename(columns=new_cols)
                cleaning_notes.append(f"Renombrado por Glosario: {len(new_cols)} columnas.")

        # =====================================================================
        # 🧠 UNIVERSAL TYPE INFERENCE V7 (Content-Driven, Zero Hardcoded Names)
        # Rule: We inspect DATA CONTENT, not column names.
        # =====================================================================
        for col in main_df.columns:
            if col.startswith('_') or col == 'is_latest_snapshot':
                continue
            
            # Skip columns already sanitized by entropy
            if col in entropy_notes:
                continue

            # === A. OBJECT COLUMNS: Infer type from content ===
            if main_df[col].dtype == 'object':
                # 🚀 [EARLY ID SHIELD V4] Rule 1.2: Protect semantic IDs BEFORE any numeric/date testing
                # Si es un ID por nombre, lo convertimos a string inmediatamente. Evita que Pandas 
                # transforme códigos numéricos en floats o reemplace nulos con 0.
                is_semantic_id = DataEngine._has_identifier_semantic_name(col)
                if is_semantic_id:
                    print(f"🛡️ [EARLY ID SHIELD] '{col}' protegido por Nombre Semántico → Forzando a String (Sin Asignar)")
                    main_df[col] = main_df[col].astype(str).str.strip().str.title()
                    # 🛡️ REGLA ESTRÍCTA: Los valores nulos (NaN) en IDs no son ceros. Son "Sin Asignar".
                    main_df[col] = main_df[col].replace(['Nan', 'nan', 'None', '', '<Na>', 'Nat', '<na>', '0.0'], 'Sin Asignar')
                    continue

                sample = main_df[col].dropna().astype(str).str.strip()
                sample = sample[sample != '']
                if sample.empty:
                    continue
                
                sample_size = max(len(sample), 1)
                total_rows = max(len(main_df), 1)
                cardinality = main_df[col].nunique()
                cardinality_ratio = cardinality / total_rows
                
                # --- A1. Try DATE parse first (dates can look numeric: 20240115) ---
                date_parsed = pd.to_datetime(sample, errors='coerce', infer_datetime_format=True)
                date_ratio = date_parsed.notna().sum() / sample_size
                
                if date_ratio > 0.6:
                    unparseable_ratio = 1 - date_ratio
                    has_alpha = sample.str.contains(r'[a-zA-Z]', regex=True).sum() / sample_size
                    
                    # 🛡️ MIXED CONTENT GUARD (Layer 1): If >5% of values fail to parse,
                    # the column has mixed content (e.g., "SALDOS" + "010731") → keep as string
                    if unparseable_ratio > 0.05:
                        print(f"🛡️ [MIXED CONTENT GUARD] '{col}' tiene contenido mixto ({unparseable_ratio:.0%} no-parseable) → String preservado")
                        continue
                    
                    # 🛡️ DATE GUARD (Layer 2): Pure-numeric codes with high cardinality
                    # that happen to parse as dates (e.g., "010731" → 2007-31-01)
                    elif has_alpha < 0.1 and cardinality_ratio > 0.5 and cardinality > 100:
                        print(f"🛡️ [DATE GUARD] '{col}' parece fecha (ratio: {date_ratio:.2f}) pero alta cardinalidad ({cardinality}) → Código, no fecha")
                        continue
                    
                    else:
                        # Real dates — safe to convert
                        main_df[col] = pd.to_datetime(main_df[col], errors='coerce')
                        print(f"📅 [TYPE INFERENCE] '{col}' → FECHA (date_ratio: {date_ratio:.2f})")
                        continue
                
                # --- A2. Try NUMERIC parse ---
                # 🛡️ TEXT GUARD V8: Before stripping non-digits, check if original values
                # contain alphabetic WORDS. If >30% of samples have letter sequences (2+ chars),
                # it's descriptive text even if stripping produces numbers.
                # Example: 'Chocolisto 6tarr x1000g gtis croc50g PE' → stripping gives '61000050',
                # but it's clearly a product DESCRIPTION, not a number.
                has_words = sample.str.contains(r'[a-zA-ZáéíóúñÑÁÉÍÓÚ]{2,}', regex=True)
                word_ratio = has_words.sum() / sample_size
                
                if word_ratio > 0.3:
                    # This column contains words → it's TEXT, not numeric
                    print(f"🛡️ [TEXT GUARD V8] '{col}' contiene palabras (word_ratio: {word_ratio:.2f}) → Texto preservado")
                else:
                    clean_sample = sample.str.replace(r'[^\d.,-]', '', regex=True)
                    nums_test = pd.to_numeric(
                        clean_sample.str.replace(',', '.', regex=False), 
                        errors='coerce'
                    )
                    num_ratio = nums_test.notna().sum() / sample_size
                    
                    if num_ratio > 0.8:
                        # 🛡️ UNIVERSAL ID SHIELD V3 (Data-Driven)
                        # Identificadores matemáticos implícitos invisibles: Si >90% único y >20 únicos
                        if DataEngine._should_force_identifier_from_uniqueness(
                            col,
                            sample,
                            cardinality,
                            cardinality_ratio,
                        ):
                            print(f"🛡️ [ID SHIELD V3] '{col}' protegida (Comportamiento ID: {cardinality_ratio:.0%} únicos) → Forzando a String (Sin Asignar)")
                            main_df[col] = main_df[col].astype(str).str.strip().str.title()
                            main_df[col] = main_df[col].replace(['Nan', 'nan', 'None', '', '<Na>', 'Nat', '<na>', '0.0'], 'Sin Asignar')
                            continue
                        
                        print(f"📊 [TYPE INFERENCE] '{col}' → NÚMERO (num_ratio: {num_ratio:.2f})")
                        
                        # I. Real cleanup
                        col_clean = main_df[col].astype(str).str.replace(r'[^\d.,-]', '', regex=True)
                        
                        # II. Regional detection (Dot vs Comma as decimal)
                        has_comma = col_clean.str.contains(',').sum()
                        has_dot = col_clean.str.contains(r'\.').sum()
                        if has_comma > has_dot:
                            col_clean = col_clean.str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
                        else:
                            col_clean = col_clean.str.replace(',', '', regex=False)
                        
                        # III. Final conversion
                        main_df[col] = pd.to_numeric(col_clean, errors='coerce').fillna(0)
                    else:
                        # --- A3. It's TEXT: Clean it ---
                        main_df[col] = main_df[col].astype(str).str.strip().str.title().replace('Nan', '')

        # =====================================================================
        # 🛡️ [FASE 4] DATA SHIELD: Deep Pre-Parquet Sanitization
        # Runs before schema profiling to ensure Parquet files are pristine.
        # Catches: NaN poison, duplicate cols, empty rows, name collisions.
        # =====================================================================
        _shield_notes = []
        
        # A. Remove fully-empty rows (all NaN/None/empty-string)
        row_count_before = len(main_df)
        main_df = main_df.replace('', np.nan)  # Normalize empty strings to NaN first
        main_df = main_df.dropna(how='all')
        rows_dropped = row_count_before - len(main_df)
        if rows_dropped > 0:
            _shield_notes.append(f"Filas completamente vacías eliminadas: {rows_dropped}")
            print(f"🛡️ [DATA SHIELD] {rows_dropped} filas vacías eliminadas ({rows_dropped/max(row_count_before,1)*100:.1f}%)")
        
        # B. Remove duplicate column names (keep first occurrence)
        if main_df.columns.duplicated().any():
            dup_cols = main_df.columns[main_df.columns.duplicated()].tolist()
            main_df = main_df.loc[:, ~main_df.columns.duplicated()]
            _shield_notes.append(f"Columnas duplicadas eliminadas: {dup_cols}")
            print(f"🛡️ [DATA SHIELD] Columnas duplicadas eliminadas: {dup_cols}")
        
        # C. Sanitize column names to be Parquet-safe (no spaces, special chars)
        # Remove any remaining problematic characters that could break Ibis/DuckDB SQL
        safe_cols = {}
        for col in main_df.columns:
            safe_name = re.sub(r'[^\w]', '_', str(col))  # Only alphanumeric + underscore
            safe_name = re.sub(r'_+', '_', safe_name).strip('_')  # Collapse multiple underscores
            if not safe_name:
                safe_name = f"columna_{main_df.columns.get_loc(col)}"
            if safe_name != col:
                safe_cols[col] = safe_name
        if safe_cols:
            main_df = main_df.rename(columns=safe_cols)
            print(f"🛡️ [DATA SHIELD] Columnas renombradas para seguridad Parquet: {safe_cols}")
        
        # D. Fill remaining NaN in numeric columns with 0 (prevents Parquet serialization issues)
        for col in main_df.select_dtypes(include=[np.number]).columns:
            nan_count = main_df[col].isna().sum()
            if nan_count > 0:
                main_df[col] = main_df[col].fillna(0)
                print(f"🛡️ [DATA SHIELD] Columna numérica '{col}': {nan_count} NaN → 0")
        
        # E. Fill remaining NaN in string/object columns with empty string
        for col in main_df.select_dtypes(include=['object', 'string']).columns:
            nan_count = main_df[col].isna().sum()
            if nan_count > 0:
                main_df[col] = main_df[col].fillna('')
        
        if _shield_notes:
            cleaning_notes.append(f"Data Shield: {'; '.join(_shield_notes)}")
        
        # =====================================================================
        # 🧬 SCHEMA PROFILE (Universal Column Classification)
        # This replaces the old hardcoded _detect_topology for upstream consumers.
        # =====================================================================
        schema_profile = DataEngine._classify_columns(main_df)
        
        # [FASE 4C] STRICT ID CASTING (The Anti-Metric Shield)
        # Force cast to STRING for any column identified as 'categorical' or 'identifier'
        # This prevents Gemini from treating IDs (like '130', '810') as metrics/numbers.
        for col, info in schema_profile.items():
            if info['role'] in ['dimension', 'identifier'] or info['type'] in ['categorical', 'id']:
                if col in main_df.columns:
                    # Check if it was numeric before (or object that looks numeric)
                    # We cast ALL dimensions to string to be safe.
                    if pd.api.types.is_numeric_dtype(main_df[col]):
                        print(f"🛡️ [STRICT ID CASTING] Forzando '{col}' a STRING (Role: {info['role']})")
                        # Use .astype(str) but replace 'nan' with ''
                        main_df[col] = main_df[col].astype(str).replace('nan', '')
                        # Update schema info to reflect the change
                        schema_profile[col]['type'] = 'categorical'

        dimension_canonicalization = {}
        main_df, dimension_canonicalization = DataEngine._apply_dimension_canonicalization(main_df, schema_profile)
        if dimension_canonicalization:
            cleaning_notes.append(
                "Canonical Dimensions: "
                + '; '.join(
                    f"{col} (merged={meta['collapsed_variant_groups']}, groups={meta['canonical_groups']})"
                    for col, meta in dimension_canonicalization.items()
                )
            )
            schema_profile = DataEngine._classify_columns(main_df)

        topology_rules = DataEngine._detect_topology(main_df, schema_profile)
        semantic_contract = DataEngine._infer_dataset_semantic_contract(main_df, schema_profile, topology_rules)
        if dimension_canonicalization:
            semantic_contract['canonical_dimensions'] = dimension_canonicalization
        main_df.attrs['semantic_contract'] = semantic_contract

        analytical_contract = AnalyticalContract.from_legacy_dict(
            dataset_contract=semantic_contract,
        )
        main_df.attrs['analytical_contract'] = analytical_contract
        
        # --- 📸 SNAPSHOT LOGIC INJECTION V8 (Contract-Driven) ---
        # Solo crea `is_latest_snapshot` cuando el contrato del dataset lo autoriza.
        metric_cols = [c for c, info in schema_profile.items() if info['role'] == 'metric']
        date_cols = [c for c, info in schema_profile.items() if info['role'] == 'date']

        if semantic_contract.get('snapshot_guard_allowed') and metric_cols and date_cols:
            time_axis = semantic_contract.get('time_axis') or date_cols[0]
            if time_axis in main_df.columns and pd.api.types.is_datetime64_any_dtype(main_df[time_axis]):
                max_date = main_df[time_axis].max()
                main_df['is_latest_snapshot'] = main_df[time_axis] == max_date
                
                print(f"📸 [SNAPSHOT LOGIC V8] Última foto habilitada en '{time_axis}': {max_date}")
                cleaning_notes.append(f"Snapshot Logic: Flag 'is_latest_snapshot' (Max Date: {max_date}).")
        else:
            print(
                "🧠 [SNAPSHOT LOGIC V8] Omitido: "
                f"dataset_mode={semantic_contract.get('dataset_mode')} "
                f"| snapshot_guard={semantic_contract.get('snapshot_guard_allowed')}"
            )
            cleaning_notes.append(
                "Snapshot Logic: Omitted by semantic contract "
                f"(Mode: {semantic_contract.get('dataset_mode')})."
            )

        main_df.attrs['schema_profile'] = schema_profile
        main_df.attrs['topology_rules'] = topology_rules
        main_df.attrs['currency_meta'] = currency_meta
        main_df.attrs['literal_filter_catalog'] = DataEngine._build_literal_filter_catalog(main_df, schema_profile)
        main_df.attrs['translator_context_summary'] = DataEngine._build_translator_context_summary(schema_profile, topology_rules)
        main_df.attrs['reference_date'] = DataEngine._detect_reference_date(main_df, schema_profile, semantic_contract)
        # [MEJORA 6 2026-09] Exponer reference_date también en el contrato (aditivo).
        if semantic_contract.get('reference_date') is None:
            semantic_contract['reference_date'] = main_df.attrs['reference_date']
        main_df.attrs['cleaning_notes'] = "\n".join(cleaning_notes)

        # Return includes schema_profile for downstream consumers (IbisEngine, SemanticTranslator)
        return main_df, topology_rules, "\n".join(cleaning_notes), currency_meta, schema_profile

    @staticmethod
    def _detect_topology(df, schema_profile: dict = None):
        """
        Topology Detector V7 — Data-Driven.
        Uses schema_profile to classify columns instead of keyword matching.
        Falls back to basic heuristics if schema_profile is not provided.
        """
        rules = {}
        if schema_profile is None:
            schema_profile = DataEngine._classify_columns(df)
        
        # Find date columns from schema
        date_cols = [c for c, info in schema_profile.items() if info['type'] == 'temporal']
        has_temporal = len(date_cols) > 0
        
        # Ensure date columns are actually datetime typed
        for dc in date_cols:
            if not pd.api.types.is_datetime64_any_dtype(df[dc]):
                df[dc] = pd.to_datetime(df[dc], errors='coerce')
        
        # Classify each metric column
        for col, info in schema_profile.items():
            if info['role'] != 'metric':
                continue
            
            # --- DATA-DRIVEN UNIT DETECTION ---
            # Instead of keyword matching, we use statistical properties:
            cl = col.lower()
            col_data = df[col].dropna()
            
            if col_data.empty:
                rules[col] = "UNKNOWN | UNIT: NUMBER"
                continue
            
            # 🛡️ DEFENSIVE: Skip non-numeric columns that slipped through
            if not pd.api.types.is_numeric_dtype(col_data):
                rules[col] = "CATEGORICAL | ROLE: DIMENSION"
                continue
            
            # Heuristic 1: Percentage detection (all values 0-100 or 0-1)
            col_max = col_data.max()
            col_min = col_data.min()
            
            if 0 <= col_min and col_max <= 1 and col_data.mean() < 1:
                unit_tag = "PERCENTAGE"
            elif 0 <= col_min and col_max <= 100 and '%' in cl:
                unit_tag = "PERCENTAGE"
            else:
                # Default: let Gemini (SemanticTranslator) decide currency vs quantity.
                # We provide the data characteristics, not keyword assumptions.
                unit_tag = "NUMBER"
            
            # --- DATA-DRIVEN TOPOLOGY (Snapshot vs Flow) ---
            # A column is SNAPSHOT if: values don't grow monotonically and cardinality is low relative to rows.
            # A column is FLOW if: values can be meaningfully summed over time.
            # Without domain knowledge, we default to FLOW (safe for most analyses).
            topology_type = "FLOW (Sumable)"  # Safe default
            
            if has_temporal and len(col_data) > 10:
                # Check if values are roughly constant over time (snapshot behavior)
                # A snapshot metric has similar values across time periods.
                col_std = col_data.std()
                col_mean = abs(col_data.mean()) if col_data.mean() != 0 else 1
                cv = col_std / col_mean  # Coefficient of variation
                
                if cv < 0.1:  # Very low variation → likely a snapshot/balance
                    topology_type = "SNAPSHOT (No sumar en tiempo)"
            
            rules[col] = f"{topology_type} | UNIT: {unit_tag}"
        
        # Add dimension info
        for col, info in schema_profile.items():
            if info['role'] == 'dimension':
                rules[col] = f"DIMENSION | CARDINALITY: {info['cardinality']}"
            elif info['role'] == 'date':
                rules[col] = f"TEMPORAL | ROLE: DATE"
            elif info['role'] == 'identifier':
                rules[col] = f"IDENTIFIER | PROTECTED"
        
        return rules

    @staticmethod
    def _to_json_safe(value):
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json") if hasattr(value, "model_dump") else value.dict()
        return value

    @staticmethod
    def _normalize_dimension_text_value(value) -> str:
        if pd.isna(value):
            return ''

        text = str(value).replace('\0', '')
        text = unicodedata.normalize('NFKC', text)
        text = re.sub(r'\s+', ' ', text).strip()

        if text.lower() in {'nan', 'none', '<na>', '<nat>', 'nat'}:
            return ''

        return text

    @staticmethod
    def _canonicalize_dimension_text_value(value) -> str:
        normalized = DataEngine._normalize_dimension_text_value(value)
        if not normalized:
            return ''

        normalized = unicodedata.normalize('NFKD', normalized)
        normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = re.sub(r'[^\w\s-]', ' ', normalized, flags=re.UNICODE)
        normalized = normalized.lower()
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    @staticmethod
    def _humanize_dimension_display_value(value: str) -> str:
        normalized = DataEngine._normalize_dimension_text_value(value)
        if not normalized:
            return ''

        letters_only = re.sub(r'[^A-Za-zÁÉÍÓÚáéíóúÑñ]', '', normalized)
        if not letters_only:
            return normalized

        if normalized == normalized.upper():
            if len(letters_only) <= 3 and ' ' not in normalized:
                return normalized
            return normalized.lower().title()

        if normalized == normalized.lower():
            return normalized.title()

        return normalized

    @staticmethod
    def _display_label_score(value: str) -> tuple[int, int, int]:
        normalized = DataEngine._normalize_dimension_text_value(value)
        if not normalized:
            return (-1, -1, -1)

        letters_only = re.sub(r'[^A-Za-zÁÉÍÓÚáéíóúÑñ]', '', normalized)
        score = 0

        if normalized != normalized.upper() and normalized != normalized.lower():
            score += 2
        elif normalized and normalized[0].isupper():
            score += 1

        if normalized == normalized.upper() and len(letters_only) > 3:
            score -= 1

        return (score, len(letters_only), -len(normalized))

    @staticmethod
    def _apply_dimension_canonicalization(df: pd.DataFrame, schema_profile: dict) -> tuple[pd.DataFrame, dict]:
        """
        Consolida variantes textuales de dimensiones (case/espacios/acentos/puntuación)
        sin tocar identificadores. El agrupamiento futuro opera sobre una etiqueta estable.
        """
        notes = {}

        if df is None or df.empty or not schema_profile:
            return df, notes

        for col, info in schema_profile.items():
            if info.get('role') != 'dimension' or col not in df.columns:
                continue

            if not (
                pd.api.types.is_object_dtype(df[col]) or
                pd.api.types.is_string_dtype(df[col]) or
                pd.api.types.is_categorical_dtype(df[col])
            ):
                continue

            normalized_series = df[col].apply(DataEngine._normalize_dimension_text_value)
            non_empty = normalized_series[normalized_series != '']
            if non_empty.empty:
                continue

            raw_counts = non_empty.value_counts(dropna=True)
            variants_by_key: dict[str, list[str]] = {}

            for raw_value in raw_counts.index.tolist():
                canonical_key = DataEngine._canonicalize_dimension_text_value(raw_value)
                if not canonical_key:
                    continue
                variants_by_key.setdefault(canonical_key, []).append(raw_value)

            label_map = {}
            collapsed_groups = 0
            transformed_values = 0

            for canonical_key, variants in variants_by_key.items():
                ranked_variants = sorted(
                    variants,
                    key=lambda variant: (
                        -int(raw_counts.get(variant, 0)),
                        -DataEngine._display_label_score(variant)[0],
                        -DataEngine._display_label_score(variant)[1],
                        variant,
                    ),
                )
                preferred_raw = ranked_variants[0]
                preferred_label = DataEngine._humanize_dimension_display_value(preferred_raw)
                label_map[canonical_key] = preferred_label

                if len(variants) > 1:
                    collapsed_groups += 1
                if preferred_label != preferred_raw:
                    transformed_values += 1

            canonicalized_series = normalized_series.apply(
                lambda value: label_map.get(
                    DataEngine._canonicalize_dimension_text_value(value),
                    DataEngine._humanize_dimension_display_value(value),
                ) if value != '' else ''
            )

            if canonicalized_series.equals(normalized_series):
                continue

            df[col] = canonicalized_series
            notes[col] = {
                'canonical_groups': len(label_map),
                'collapsed_variant_groups': collapsed_groups,
                'humanized_values': transformed_values,
            }
            print(
                f"🧹 [DIMENSION CANONICALIZER] '{col}' normalizada | "
                f"groups={len(label_map)} | merged={collapsed_groups} | humanized={transformed_values}"
            )

        return df, notes

    @staticmethod
    def _select_period_time_axis(df: pd.DataFrame, date_cols: list[str]) -> str | None:
        """
        Selecciona el eje temporal que mejor se comporta como eje de PERIODO
        (snapshot) en lugar de tomar posicionalmente la primera columna fecha.

        Coherencia con 'Snapshot Isolation': un archivo puede tener una fecha
        de corte (pocos valores, muchas filas por corte) y una fecha accesoria
        de alta cardinalidad (caducidad, vencimiento, registro). Elegir la
        primera por posición puede escoger la accesoria y colapsar el análisis
        a una rebanada irrelevante.

        Criterio data-driven (schema-agnostic, sin hardcode de nombres): la
        densidad de filas por valor distinto (`avg_rows_per_period`) es máxima
        en el eje que particiona el dataset en cortes comparables. Se desempata
        por filas en la fecha máxima y, finalmente, por orden original.
        """
        candidates = [col for col in date_cols if col in df.columns]
        if not candidates:
            return date_cols[0] if date_cols else None
        if len(candidates) == 1:
            return candidates[0]

        best_col = candidates[0]
        best_density = -1.0
        best_rows_at_max = -1
        for col in candidates:
            try:
                parsed = pd.to_datetime(df[col], errors='coerce')
            except Exception:
                continue
            valid = parsed.dropna()
            if valid.empty:
                continue
            unique_dates = int(valid.nunique())
            density = float(len(valid)) / max(unique_dates, 1)
            rows_at_max = int((parsed == valid.max()).fillna(False).sum())
            if density > best_density or (
                density == best_density and rows_at_max > best_rows_at_max
            ):
                best_col = col
                best_density = density
                best_rows_at_max = rows_at_max
        return best_col

    @staticmethod
    def _compute_metric_semantics(
        df: pd.DataFrame,
        metric_cols: list[str],
        axis_col: str,
        axis_order: list[str] | None = None,
    ) -> dict:
        """[Fase 1.2 2026-09] Señales estructurales por métrica (data-driven).

        Calcula la dirección monótona de cada métrica sobre el eje temporal
        disponible (tipado u ordinal). Es la señal #1 del guard de acumulados,
        ahora declarada en el contrato L1 en vez de recomputarse por consulta.
        """
        if df is None or df.empty or not metric_cols or axis_col not in df.columns:
            return {}
        semantics: dict[str, dict] = {}
        try:
            if axis_order:
                rank = {str(value): index for index, value in enumerate(axis_order)}
                ordering = df[axis_col].astype(str).map(rank)
                ordered_index = ordering.sort_values(kind="stable").index
            else:
                ordered_index = df[axis_col].sort_values(kind="stable").index
        except Exception:
            return {}
        for col in metric_cols:
            if col not in df.columns:
                continue
            direction = series_monotonic_direction(df[col].loc[ordered_index])
            if direction is not None:
                semantics[col] = {"cumulative_monotonic_direction": direction}
        return semantics

    @staticmethod
    def _infer_dataset_semantic_contract(df: pd.DataFrame, schema_profile: dict, topology_rules: dict | None = None) -> dict:
        """
        Capa contractual del dataset.
        Decide si el archivo se comporta como FLOW, SNAPSHOT o HYBRID usando
        evidencia del contenido, no solo presencia de fechas + métricas.
        """
        topology_rules = topology_rules or {}

        contract = {
            'version': DataEngine._SEMANTIC_CONTRACT_VERSION,
            'dataset_mode': 'unknown',
            'snapshot_guard_allowed': False,
            'contract_state': 'unknown',
            # F3: presentes siempre para que el merge `{**previo, **reinferido}`
            # de `_refresh_stale_semantic_contract` limpie un expiry vencido aunque
            # la reinferencia salga por un early-return (evita bucle de refresh).
            'bootstrap_expires_at': None,
            'bootstrap_scope': {},
            'time_axis': None,
            'date_columns': [],
            'metric_columns': [],
            'dimension_columns': [],
            'identifier_columns': [],
            'entity_key': None,
            # [Fase 1.1/1.2 2026-09] Eje ordinal + señales estructurales por métrica.
            'ordinal_axis': None,
            'ordinal_axis_order': [],
            'time_axis_kind': None,
            'metric_semantics': {},
            'evidence': {},
        }

        if df is None or df.empty or not schema_profile:
            return contract

        date_cols = [c for c, info in schema_profile.items() if info.get('role') == 'date' and c in df.columns]
        metric_cols = [c for c, info in schema_profile.items() if info.get('role') == 'metric' and c in df.columns]
        dimension_cols = [c for c, info in schema_profile.items() if info.get('role') == 'dimension' and c in df.columns]
        identifier_cols = [c for c, info in schema_profile.items() if info.get('role') == 'identifier' and c in df.columns]

        contract['date_columns'] = date_cols
        contract['metric_columns'] = metric_cols
        contract['dimension_columns'] = dimension_cols
        contract['identifier_columns'] = identifier_cols

        # [Fase 1.1 2026-09] Eje temporal ORDINAL (mes-texto, "2021-07", "W30").
        # Se detecta por VALORES sobre columnas no-fecha/no-métrica. No habilita
        # corte (snapshot_guard_allowed sigue False): solo expone orden temporal.
        ordinal_axis, ordinal_order = detect_ordinal_axis(
            df,
            schema_profile,
            exclude=set(date_cols) | set(metric_cols),
        )
        if ordinal_axis:
            contract['ordinal_axis'] = ordinal_axis
            contract['ordinal_axis_order'] = ordinal_order

        if not metric_cols:
            contract['time_axis_kind'] = 'ordinal' if ordinal_axis else None
            contract['evidence'] = {
                'total_rows': int(len(df)),
                'reason': 'missing_metric_columns',
                'ordinal_axis': ordinal_axis,
            }
            return contract

        if not date_cols:
            # Sin eje tipado: si hay eje ordinal, el contrato lo expone y calcula
            # las señales estructurales por métrica sobre ese orden. Sin corte.
            contract['time_axis_kind'] = 'ordinal' if ordinal_axis else None
            if ordinal_axis:
                contract['metric_semantics'] = DataEngine._compute_metric_semantics(
                    df, metric_cols, ordinal_axis, ordinal_order
                )
            contract['evidence'] = {
                'total_rows': int(len(df)),
                'reason': 'missing_date_columns',
                'ordinal_axis': ordinal_axis,
            }
            return contract

        time_axis = DataEngine._select_period_time_axis(df, date_cols) or date_cols[0]
        contract['time_axis'] = time_axis
        contract['time_axis_kind'] = 'date'
        # [Fase 1.2] Señales estructurales por métrica sobre el eje tipado.
        contract['metric_semantics'] = DataEngine._compute_metric_semantics(
            df, metric_cols, time_axis
        )
        # [MEJORA 6 2026-09] Vocabulario temporal explícito (aditivo):
        # snapshot_axis = eje de corte; event_axes = fechas accesorias distintas.
        contract['snapshot_axis'] = time_axis
        contract['event_axes'] = [c for c in date_cols if c != time_axis]

        date_series = df[time_axis]
        if not pd.api.types.is_datetime64_any_dtype(date_series):
            parsed_dates = pd.to_datetime(date_series, errors='coerce')
            valid_ratio = parsed_dates.notna().sum() / max(len(parsed_dates), 1)
            if valid_ratio > 0.6:
                date_series = parsed_dates
            else:
                contract['evidence'] = {
                    'total_rows': int(len(df)),
                    'reason': 'time_axis_not_parseable',
                    'time_axis': time_axis,
                }
                return contract

        valid_dates = date_series.dropna()
        if valid_dates.empty:
            contract['evidence'] = {
                'total_rows': int(len(df)),
                'reason': 'no_valid_dates',
                'time_axis': time_axis,
            }
            return contract

        total_rows = int(len(df))
        unique_dates = int(valid_dates.nunique())
        avg_rows_per_period = total_rows / max(unique_dates, 1)
        min_date = valid_dates.min()
        max_date = valid_dates.max()
        max_date_mask = date_series == max_date
        rows_at_max_date = int(max_date_mask.fillna(False).sum())
        rows_at_max_ratio = rows_at_max_date / max(total_rows, 1)

        primary_metric = metric_cols[0]
        metric_series = pd.to_numeric(df[primary_metric], errors='coerce').fillna(0).abs()
        metric_total = float(metric_series.sum())
        metric_at_max = float(metric_series[max_date_mask.fillna(False)].sum()) if rows_at_max_date > 0 else 0.0
        metric_at_max_ratio = metric_at_max / metric_total if metric_total > 0 else 0.0

        entity_key = None
        repeated_entity_ratio = None
        entity_period_coverage = None
        entity_key_confidence = 0.0
        entity_candidates = identifier_cols[:]
        if not entity_candidates:
            entity_candidates = [
                col for col in dimension_cols
                if schema_profile.get(col, {}).get('cardinality_ratio', 0) > 0.25
            ]

        if entity_candidates:
            entity_key = entity_candidates[0]
            contract['entity_key'] = entity_key
            entity_observations = pd.DataFrame({
                '__entity__': df[entity_key],
                '__date__': date_series,
            }).dropna(subset=['__entity__', '__date__'])
            entity_observations['__entity__'] = entity_observations['__entity__'].astype(str).str.strip()
            entity_observations = entity_observations[entity_observations['__entity__'] != '']
            if not entity_observations.empty:
                entity_date_span = entity_observations.groupby('__entity__')['__date__'].nunique()
                repeated_entity_ratio = float((entity_date_span > 1).mean())
                unique_entities = int(entity_date_span.size)
                observed_entity_periods = int(entity_observations.drop_duplicates().shape[0])
                possible_entity_periods = unique_entities * unique_dates
                entity_period_coverage = (
                    observed_entity_periods / possible_entity_periods
                    if possible_entity_periods > 0 else None
                )
                entity_cardinality_ratio = unique_entities / max(len(entity_observations), 1)
                # La confianza requiere una clave que sea a la vez distinguible y recurrente.
                entity_key_confidence = (
                    0.55 * repeated_entity_ratio
                    + 0.30 * max(0.0, 1.0 - entity_cardinality_ratio)
                    + 0.15 * min(1.0, entity_cardinality_ratio / 0.02)
                )

        rows_per_period = valid_dates.value_counts(dropna=True)
        rows_per_period_cv = None
        if not rows_per_period.empty and float(rows_per_period.mean()) > 0:
            rows_per_period_cv = float(rows_per_period.std(ddof=0) / rows_per_period.mean())

        semantic_snapshot_hints = (
            ' '.join(metric_cols + dimension_cols + identifier_cols).lower()
        )
        snapshot_keywords = ['stock', 'inventario', 'saldo', 'balance', 'disponible', 'existencia', 'on_hand']
        flow_keywords = ['venta', 'sales', 'sold', 'ingreso', 'revenue', 'transaction', 'transaccion', 'pedido', 'order']

        snapshot_score = 0
        flow_score = 0
        score_reasons = []

        if unique_dates >= 2:
            snapshot_score += 1
            score_reasons.append('multi_period_dataset')

        if avg_rows_per_period <= 2:
            flow_score += 2
            score_reasons.append(f'avg_rows_per_period={avg_rows_per_period:.2f}')

        if rows_at_max_ratio >= 0.08 and avg_rows_per_period > 3:
            snapshot_score += 2
            score_reasons.append(f'rows_at_max_ratio={rows_at_max_ratio:.3f}')
        elif rows_at_max_ratio <= 0.03:
            flow_score += 2
            score_reasons.append(f'rows_at_max_ratio={rows_at_max_ratio:.3f}')

        if repeated_entity_ratio is not None:
            if repeated_entity_ratio >= 0.25:
                snapshot_score += 2
                score_reasons.append(f'repeated_entity_ratio={repeated_entity_ratio:.3f}')
            elif repeated_entity_ratio <= 0.10:
                flow_score += 2
                score_reasons.append(f'repeated_entity_ratio={repeated_entity_ratio:.3f}')

        if metric_at_max_ratio >= 0.10:
            snapshot_score += 1
            score_reasons.append(f'metric_at_max_ratio={metric_at_max_ratio:.3f}')
        elif 0 < metric_at_max_ratio <= 0.10:
            flow_score += 1
            score_reasons.append(f'metric_at_max_ratio={metric_at_max_ratio:.3f}')

        if any(keyword in semantic_snapshot_hints for keyword in snapshot_keywords):
            snapshot_score += 1
            score_reasons.append('snapshot_semantic_hint')

        if any(keyword in semantic_snapshot_hints for keyword in flow_keywords):
            flow_score += 1
            score_reasons.append('flow_semantic_hint')

        if '_virtual_snapshot_date_' in df.columns and repeated_entity_ratio and repeated_entity_ratio >= 0.25:
            snapshot_score += 1
            score_reasons.append('virtual_snapshot_series')

        if snapshot_score >= 4 and snapshot_score > flow_score:
            dataset_mode = 'snapshot'
        elif flow_score >= 4 and flow_score > snapshot_score:
            dataset_mode = 'flow'
        elif snapshot_score >= 3 and flow_score >= 3:
            dataset_mode = 'hybrid'
        elif snapshot_score > flow_score:
            dataset_mode = 'snapshot'
        elif flow_score > snapshot_score:
            dataset_mode = 'flow'
        else:
            dataset_mode = 'undetermined'

        legacy_dataset_mode = dataset_mode
        confidence_threshold = settings.SEMANTIC_CONTRACT_ENTITY_KEY_CONFIDENCE_THRESHOLD
        confidence_version = settings.SEMANTIC_CONTRACT_ENTITY_KEY_CONFIDENCE_VERSION
        threshold_is_valid = isinstance(confidence_threshold, (int, float)) and 0.0 <= confidence_threshold <= 1.0
        configuration_is_valid = threshold_is_valid and bool(confidence_version)
        gate_enabled = bool(settings.SEMANTIC_CONTRACT_F0_GATE)
        bridge_votes = sum((
            entity_period_coverage is not None and entity_period_coverage >= 0.60,
            repeated_entity_ratio is not None and repeated_entity_ratio >= 0.25,
            rows_per_period_cv is not None and rows_per_period_cv <= 0.20,
        ))
        veto_reasons = []
        if entity_period_coverage is None or entity_period_coverage <= 0.25:
            veto_reasons.append('entity_period_coverage')
        if repeated_entity_ratio is None or repeated_entity_ratio <= 0.10:
            veto_reasons.append('repeated_entity_ratio')
        if not threshold_is_valid or entity_key_confidence < float(confidence_threshold):
            veto_reasons.append('entity_key_confidence')

        allow_cut_legacy = (
            gate_enabled
            and configuration_is_valid
            and legacy_dataset_mode == 'snapshot'
            and not veto_reasons
            and entity_key_confidence >= float(confidence_threshold)
            and bridge_votes >= 2
        )
        # [F2] Confirmación: la evidencia F0 fuerte (los TRES votos del puente)
        # eleva el contrato del puente `legacy_inferred` a `confirmed_file_contract`.
        # No cambia `dataset_mode` ni `snapshot_guard_allowed`: el corte es idéntico.
        f2_confirm_enabled = bool(settings.SEMANTIC_CONTRACT_F2_CONFIRM)
        # [F3] Bootstrap verificado (time-boxed): el puente que pasa el gate pero
        # no alcanza confirmación fuerte se marca como provisional con expiración,
        # en vez de un `legacy_inferred` abierto. El corte no cambia.
        f3_bootstrap_enabled = bool(settings.SEMANTIC_CONTRACT_F3_BOOTSTRAP)
        bootstrap_expires_at: str | None = None
        bootstrap_scope: dict = {}
        contract_confirmed = False
        confirmation_reasons: list[str] = []
        if gate_enabled and legacy_dataset_mode in {'snapshot', 'hybrid', 'undetermined'}:
            if allow_cut_legacy:
                dataset_mode = 'snapshot'
                snapshot_guard_allowed = True
                if f2_confirm_enabled and bridge_votes == 3:
                    contract_state = 'confirmed_file_contract'
                    contract_confirmed = True
                    confirmation_reasons = ['allow_cut_legacy', 'bridge_votes=3']
                elif f3_bootstrap_enabled:
                    contract_state = 'bootstrap_verified'
                    bootstrap_expires_at = compute_bootstrap_expiry(
                        ttl_hours=settings.SEMANTIC_CONTRACT_BOOTSTRAP_TTL_HOURS
                    )
                    bootstrap_scope = {'origin': 'f0_bridge', 'file_scope': True}
                else:
                    contract_state = 'legacy_inferred'
            else:
                dataset_mode = 'unknown'
                snapshot_guard_allowed = False
                contract_state = 'unknown'
        else:
            snapshot_guard_allowed = legacy_dataset_mode in {'snapshot', 'hybrid'}
            contract_state = 'legacy_inferred'

        # [F4] Retiro del puente: con el flag activo, SOLO la evidencia fuerte
        # (`confirmed_file_contract`) o un template de tenant confiable concede
        # corte. `legacy_inferred` y `bootstrap_verified` dejan de habilitarlo y
        # pasan a `unknown` (fail-closed). Default OFF = comportamiento F0/F2/F3
        # byte-idéntico (inercia total). El retiro nunca concede corte: solo lo
        # retira cuando el gate F0 está activo.
        f4_retire_enabled = bool(settings.SEMANTIC_CONTRACT_F4_RETIRE_BRIDGE)
        bridge_retired = (
            gate_enabled
            and f4_retire_enabled
            and contract_state not in {'confirmed_file_contract', 'trusted_tenant_template'}
        )
        if bridge_retired:
            dataset_mode = 'unknown'
            snapshot_guard_allowed = False
            contract_state = 'unknown'
            bootstrap_expires_at = None
            bootstrap_scope = {}
            contract_confirmed = False
            confirmation_reasons = []

        contract['dataset_mode'] = dataset_mode
        contract['snapshot_guard_allowed'] = snapshot_guard_allowed
        contract['contract_state'] = contract_state
        contract['bootstrap_expires_at'] = bootstrap_expires_at
        contract['bootstrap_scope'] = bootstrap_scope
        contract['evidence'] = {
            'total_rows': total_rows,
            'unique_dates': unique_dates,
            'avg_rows_per_period': round(avg_rows_per_period, 4),
            'min_date': DataEngine._to_json_safe(min_date),
            'max_date': DataEngine._to_json_safe(max_date),
            'rows_at_max_date': rows_at_max_date,
            'rows_at_max_ratio': round(rows_at_max_ratio, 4),
            'primary_metric': primary_metric,
            'metric_at_max_ratio': round(metric_at_max_ratio, 4),
            'repeated_entity_ratio': round(repeated_entity_ratio, 4) if repeated_entity_ratio is not None else None,
            'entity_period_coverage': round(entity_period_coverage, 4) if entity_period_coverage is not None else None,
            'rows_per_period_cv': round(rows_per_period_cv, 4) if rows_per_period_cv is not None else None,
            'entity_key_confidence': round(entity_key_confidence, 4),
            'entity_key_confidence_threshold': confidence_threshold if threshold_is_valid else None,
            'entity_key_confidence_version': confidence_version or None,
            'legacy_dataset_mode': legacy_dataset_mode,
            'bridge_votes': bridge_votes,
            'veto_reasons': veto_reasons,
            'allow_cut_legacy': allow_cut_legacy,
            'contract_confirmed': contract_confirmed,
            'confirmation_reasons': confirmation_reasons,
            'bootstrap_expires_at': bootstrap_expires_at,
            'bridge_retired': bridge_retired,
            'snapshot_score': snapshot_score,
            'flow_score': flow_score,
            'score_reasons': score_reasons,
        }

        emit_structured_log(
            'semantic_contract_f0_gate_decision',
            contract_state=contract_state,
            dataset_mode=dataset_mode,
            legacy_dataset_mode=legacy_dataset_mode,
            allow_cut_legacy=allow_cut_legacy,
            contract_confirmed=contract_confirmed,
            bridge_votes=bridge_votes,
            veto_reasons=veto_reasons,
            entity_key_confidence=round(entity_key_confidence, 4),
            confidence_version=confidence_version or None,
        )

        return contract

    @staticmethod
    def _cache_paths_from_file_id(file_id: str) -> tuple[str, str]:
        cache_dir = "/tmp/promdata_cache"
        os.makedirs(cache_dir, exist_ok=True)
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(file_id))
        if not safe_id:
            safe_id = "unknown_file"
        parquet_path = os.path.join(cache_dir, f"{safe_id}.parquet")
        contract_path = DataEngine._contract_path_from_parquet_path(parquet_path)
        return parquet_path, contract_path

    @staticmethod
    def _build_literal_filter_catalog(df: pd.DataFrame, schema_profile: dict) -> dict:
        catalog = {}
        if df is None or df.empty or not schema_profile:
            return catalog

        for col_name, col_info in schema_profile.items():
            if col_info.get('role') != 'dimension' or col_name not in df.columns:
                continue
            try:
                cardinality = int(col_info.get('cardinality') or 0)
                sample_len = df[col_name].dropna().astype(str).str.len().mean()
                limit = 10000 if (sample_len or 0) < 50 else 1000
                if 0 < cardinality <= limit:
                    unique_vals = df[col_name].dropna().astype(str).replace('', np.nan).dropna().unique().tolist()
                    if unique_vals:
                        catalog[col_name] = unique_vals
            except Exception:
                continue
        return catalog

    @staticmethod
    def _build_translator_context_summary(schema_profile: dict, topology_rules: dict) -> str:
        if not schema_profile:
            return str(topology_rules or {})

        enriched_summary = {}
        for col, info in schema_profile.items():
            role_tag = info.get('role')
            if role_tag == 'dimension':
                cardinality = int(info.get('cardinality') or 0)
                if cardinality > 50:
                    role_tag = f"dimension [ENTITY/ID] (Card: {cardinality})"
                else:
                    role_tag = f"dimension [ATTRIBUTE] (Card: {cardinality})"
            enriched_summary[col] = f"{info.get('type')} | Role: {role_tag}"

        return f"SCHEMA (Semantic Tags): {enriched_summary}\nTOPOLOGY: {topology_rules}"

    @staticmethod
    def _detect_reference_date(df: pd.DataFrame, schema_profile: dict, semantic_contract: dict) -> str:
        if df is None or df.empty:
            result = str(pd.Timestamp.now().date())
            emit_structured_log("reference_date_detected", reference_date=result, method="system_fallback")
            return result

        # 1. Intentar columnas con role='date' en schema_profile (más confiable)
        date_cols = [
            c for c, info in schema_profile.items()
            if info.get('role') == 'date' and c in df.columns
        ]
        for ref_col in date_cols:
            try:
                col = df[ref_col]
                if pd.api.types.is_datetime64_any_dtype(col):
                    max_date = col.max()
                    result = str(max_date.date()) if pd.notna(max_date) else None
                else:
                    result = str(pd.to_datetime(col, errors='coerce').max().date())
                if result:
                    emit_structured_log("reference_date_detected", reference_date=result, method="role_date")
                    return result
            except Exception:
                continue

        # 2. Fallback: cualquier columna con min/max ISO en schema_profile
        for col, info in schema_profile.items():
            if not isinstance(info, dict) or col not in df.columns:
                continue
            for key in ('max', 'min'):
                raw = info.get(key)
                if raw and re.match(r'\d{4}-\d{2}-\d{2}', str(raw)):
                    try:
                        result = str(pd.to_datetime(df[col], errors='coerce').max().date())
                        emit_structured_log("reference_date_detected", reference_date=result, method="fallback_iso")
                        return result
                    except Exception:
                        continue

        result = str(pd.Timestamp.now().date())
        emit_structured_log("reference_date_detected", reference_date=result, method="system_fallback")
        return result

    @staticmethod
    def _contract_path_from_parquet_path(parquet_path: str) -> str:
        base_path, _ = os.path.splitext(parquet_path)
        return f"{base_path}.contract.json"

    @staticmethod
    def _snapshot_arrow_cache_path_from_parquet_path(parquet_path: str) -> str:
        base_path, _ = os.path.splitext(parquet_path)
        return f"{base_path}.snapshot.arrow.b64"

    @staticmethod
    def _write_sidecar_payload(contract_path: str, payload: dict) -> None:
        """Publica el sidecar completo de forma atómica para no exponer contratos parciales."""
        temporary_path = f"{contract_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(temporary_path, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, default=DataEngine._to_json_safe)
            os.replace(temporary_path, contract_path)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

    @classmethod
    def _sidecar_refresh_lock(cls, parquet_path: str) -> threading.Lock:
        with cls._sidecar_refresh_locks_guard:
            return cls._sidecar_refresh_locks.setdefault(parquet_path, threading.Lock())

    @classmethod
    @contextmanager
    def _sidecar_file_lock(cls, parquet_path: str):
        """Serializa la reinferencia entre threads y procesos del mismo runtime."""
        with cls._sidecar_refresh_lock(parquet_path):
            lock_path = f"{parquet_path}.semantic-contract.lock"
            with open(lock_path, 'a+', encoding='utf-8') as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @classmethod
    def _refresh_stale_semantic_contract(cls, parquet_path: str, payload: dict | None = None) -> tuple[dict, bool]:
        """Reinfiere sidecars previos una sola vez por archivo y proceso.

        La versión del contrato forma parte de la corrección semántica: un
        resultado cacheado no puede seguir reutilizando una inferencia previa.
        """
        current_payload = payload if isinstance(payload, dict) else cls.load_sidecar_payload(parquet_path)
        if not current_payload:
            return current_payload, False
        if (
            current_payload.get('version') == cls._SEMANTIC_CONTRACT_VERSION
            and not is_bootstrap_expired(current_payload.get('bootstrap_expires_at'))
        ):
            return current_payload, False
        if not os.path.exists(parquet_path):
            return current_payload, False

        with cls._sidecar_file_lock(parquet_path):
            current_payload = cls.load_sidecar_payload(parquet_path)
            if not current_payload:
                return current_payload, False
            version_stale = current_payload.get('version') != cls._SEMANTIC_CONTRACT_VERSION
            bootstrap_expired = is_bootstrap_expired(current_payload.get('bootstrap_expires_at'))
            if not version_stale and not bootstrap_expired:
                return current_payload, False
            try:
                cached_df = pd.read_parquet(parquet_path)
            except Exception as error:
                emit_structured_log(
                    'semantic_contract_sidecar_refresh_failed',
                    level='warning',
                    parquet_path=os.path.basename(parquet_path),
                    reason='parquet_unreadable',
                    error=str(error)[:240],
                )
                return current_payload, False

            refreshed_contract = cls._infer_dataset_semantic_contract(
                cached_df,
                current_payload.get('_schema_profile', {}) or {},
                current_payload.get('_topology_rules', {}) or {},
            )
            refreshed_payload = {**current_payload, **refreshed_contract}
            try:
                cls._write_sidecar_payload(cls._contract_path_from_parquet_path(parquet_path), refreshed_payload)
            except Exception as error:
                emit_structured_log(
                    'semantic_contract_sidecar_refresh_failed',
                    level='warning',
                    parquet_path=os.path.basename(parquet_path),
                    reason='sidecar_write_failed',
                    error=str(error)[:240],
                )
                return current_payload, False

            emit_structured_log(
                'semantic_contract_sidecar_refreshed',
                previous_version=current_payload.get('version'),
                semantic_contract_version=cls._SEMANTIC_CONTRACT_VERSION,
                reason='bootstrap_expired' if bootstrap_expired else 'version_stale',
                parquet_path=os.path.basename(parquet_path),
            )
            return refreshed_payload, True

    @staticmethod
    def load_sidecar_payload(parquet_path: str) -> dict:
        if not parquet_path:
            return {}

        contract_path = DataEngine._contract_path_from_parquet_path(parquet_path)
        if not os.path.exists(contract_path):
            return {}

        try:
            with open(contract_path, 'r', encoding='utf-8') as fh:
                payload = json.load(fh)
            return payload if isinstance(payload, dict) else {}
        except Exception as e:
            print(f"⚠️ [DATA CONTRACT] No se pudo leer sidecar '{contract_path}': {e}")
            return {}

    @staticmethod
    def load_semantic_contract(parquet_path: str) -> dict:
        """
        Recupera el contrato semántico sidecar asociado al parquet.
        """
        payload = DataEngine.load_sidecar_payload(parquet_path)
        payload, _ = DataEngine._refresh_stale_semantic_contract(parquet_path, payload)
        if not payload:
            return {}
        return {
            key: value
            for key, value in payload.items()
            if not str(key).startswith('_')
        }

    @staticmethod
    def load_cached_snapshot_arrow(file_id: str) -> str | None:
        parquet_path, _ = DataEngine._cache_paths_from_file_id(file_id)
        arrow_cache_path = DataEngine._snapshot_arrow_cache_path_from_parquet_path(parquet_path)
        if not os.path.exists(arrow_cache_path):
            return None

        try:
            with open(arrow_cache_path, 'r', encoding='utf-8') as fh:
                payload = fh.read().strip()
            return payload or None
        except Exception as e:
            print(f"⚠️ [SNAPSHOT ARROW CACHE] No se pudo leer '{arrow_cache_path}': {e}")
            return None

    @staticmethod
    def persist_cached_snapshot_arrow(file_id: str, arrow_b64: str) -> bool:
        if not arrow_b64:
            return False

        parquet_path, _ = DataEngine._cache_paths_from_file_id(file_id)
        arrow_cache_path = DataEngine._snapshot_arrow_cache_path_from_parquet_path(parquet_path)

        try:
            with open(arrow_cache_path, 'w', encoding='utf-8') as fh:
                fh.write(arrow_b64)
            return True
        except Exception as e:
            print(f"⚠️ [SNAPSHOT ARROW CACHE] No se pudo persistir '{arrow_cache_path}': {e}")
            return False

    @staticmethod
    def load_cached_dataset(file_id: str):
        parquet_path, _ = DataEngine._cache_paths_from_file_id(file_id)
        if not os.path.exists(parquet_path):
            return None

        sidecar_payload = DataEngine.load_sidecar_payload(parquet_path)
        sidecar_payload, _ = DataEngine._refresh_stale_semantic_contract(parquet_path, sidecar_payload)
        if not sidecar_payload:
            return None

        try:
            cached_df = pd.read_parquet(parquet_path)
        except Exception as e:
            print(f"⚠️ [DATA ENGINE CACHE] No se pudo leer parquet cacheado '{parquet_path}': {e}")
            return None

        semantic_contract = {
            key: value
            for key, value in sidecar_payload.items()
            if not str(key).startswith('_')
        }
        cached_df.attrs['semantic_contract'] = semantic_contract
        cached_df.attrs['schema_profile'] = sidecar_payload.get('_schema_profile', {}) or {}
        cached_df.attrs['topology_rules'] = sidecar_payload.get('_topology_rules', {}) or {}
        cached_df.attrs['currency_meta'] = sidecar_payload.get('_currency_meta', {}) or {}
        cached_df.attrs['translator_context_summary'] = sidecar_payload.get('_translator_context_summary', '') or ""
        cached_df.attrs['literal_filter_catalog'] = sidecar_payload.get('_literal_filter_catalog', {}) or {}
        cached_df.attrs['reference_date'] = sidecar_payload.get('_reference_date')
        cached_df.attrs['cleaning_notes'] = sidecar_payload.get('_cleaning_notes', '')
        return cached_df, parquet_path, sidecar_payload

    @staticmethod
    def refresh_cached_semantic_contract(file_id: str) -> bool:
        """Actualiza un sidecar obsoleto; devuelve si cambió su semántica."""
        parquet_path, _ = DataEngine._cache_paths_from_file_id(file_id)
        if not os.path.exists(parquet_path):
            return False
        _, refreshed = DataEngine._refresh_stale_semantic_contract(parquet_path)
        return refreshed

    @staticmethod
    def commit_to_parquet(df: pd.DataFrame, file_id: str) -> str:
        """
        [FASE 4] Persistencia Blindada + Validación Pre/Post-Escritura.
        Guarda el DataFrame limpio en disco para que Ibis/DuckDB lo consuman.
        
        Guards:
        - Empty DataFrame rejection
        - Path traversal prevention (sanitized file_id)
        - Column count sanity check
        - File size verification post-write
        """
        file_path, contract_path = DataEngine._cache_paths_from_file_id(file_id)
        
        # 🛡️ [PRE-FLIGHT] Validation before writing
        if df is None or df.empty:
            print("🛡️ [DATA SHIELD] DataFrame vacío — Parquet NO generado.")
            return ""
        
        if len(df.columns) == 0:
            print("🛡️ [DATA SHIELD] DataFrame sin columnas — Parquet NO generado.")
            return ""
        
        if len(df.columns) > 500:
            print(f"⚠️ [DATA SHIELD] DataFrame con {len(df.columns)} columnas (sospechoso). Limitando a 200.")
            df = df.iloc[:, :200]
        
        if hasattr(df, 'attrs'):
            df.attrs = {k: v for k, v in (df.attrs or {}).items() if not isinstance(v, BaseModel)}
        
        try:
            df.to_parquet(file_path, index=False, engine='pyarrow')
            
            # 🛡️ [POST-FLIGHT] Verify the written file
            file_size = os.path.getsize(file_path)
            file_size_mb = file_size / (1024 * 1024)
            
            if file_size == 0:
                print("🛡️ [DATA SHIELD] Parquet generado con 0 bytes — archivo corrupto.")
                os.remove(file_path)
                return ""

            contract_payload = {}
            sidecar_payload = {}
            if hasattr(df, 'attrs'):
                contract_payload = df.attrs.get('semantic_contract', {}) or {}
                sidecar_payload = {
                    **contract_payload,
                    "_schema_profile": df.attrs.get('schema_profile', {}) or {},
                    "_topology_rules": df.attrs.get('topology_rules', {}) or {},
                    "_currency_meta": df.attrs.get('currency_meta', {}) or {},
                    "_translator_context_summary": df.attrs.get('translator_context_summary', '') or "",
                    "_literal_filter_catalog": df.attrs.get('literal_filter_catalog', {}) or {},
                    "_reference_date": df.attrs.get('reference_date'),
                    "_cleaning_notes": df.attrs.get('cleaning_notes', ''),
                }
            else:
                sidecar_payload = contract_payload

            DataEngine._write_sidecar_payload(contract_path, sidecar_payload)
            
            print(f"✅ [DATA ENGINE] Snapshot Parquet generado: {file_path} ({file_size_mb:.2f} MB, {len(df)} filas, {len(df.columns)} cols)")
            print(f"🧠 [DATA CONTRACT] Sidecar generado: {contract_path}")
            return file_path
        except Exception as e:
            print(f"⚠️ [DATA ENGINE] Falló persistencia Parquet: {e}")
            return ""
