# En: backend/app/tasks/analysis_pipeline/data_loader.py
"""Utility functions for data loading — extracted from analysis_tasks.py."""

from typing import Any
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import re
import io
from time import perf_counter


def _parse_utc_datetime(raw_value: Any) -> datetime | None:
    if not raw_value:
        return None
    if isinstance(raw_value, datetime):
        dt = raw_value
    else:
        candidate = str(raw_value).strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(candidate)
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _compute_queue_wait_ms(task_created_at: Any) -> int | None:
    created_at_utc = _parse_utc_datetime(task_created_at)
    if created_at_utc is None:
        return None
    now_utc = datetime.now(timezone.utc)
    return max(int((now_utc - created_at_utc).total_seconds() * 1000), 0)


def clean_business_terms(text_data: str) -> str:
    """
    Intermediario de Vocabulario V1.
    Elimina snake_case y tecnicismos antes de que Gemini lea los datos.
    """
    replacements = {
        "total_venta_pen": "Ventas Totales (S/)",
        "cantidad_vendida": "Unidades Vendidas",
        "metric_value": "Valor",
        "_virtual_snapshot_date_": "Fecha de Corte",
        "None": "N/A",
        "nan": "0"
    }

    clean_text = text_data
    for old, new in replacements.items():
        clean_text = clean_text.replace(old, new)

    def replacer(match):
        return match.group(0).replace('_', ' ').title()

    clean_text = re.sub(r'\b[a-z]+(_[a-z]+)+\b', replacer, clean_text)

    return clean_text


def fetch_team_glossary(supabase_client: Any) -> dict:
    """
    Descarga el 'Cerebro del Equipo' y clasifica el conocimiento en dos niveles:
    1. NIVEL INGENIERÍA: Sinónimos técnicos simples para limpieza de datos.
    2. NIVEL ESTRATEGIA: Reglas de negocio, fórmulas y lógica compleja para el LLM.
    """
    try:
        response = supabase_client.table('business_glossary').select('term, definition').execute()
        glossary_map = {}

        target_concepts = {
            'fecha': ['fecha', 'date', 'day', 'time', 'periodo'],
            'fecha_vencimiento': ['vencimiento', 'caducidad', 'expiración', 'expiry'],
            'stock': ['stock', 'inventario', 'existencia', 'saldo', 'disponible', 'on_hand'],
            'sku': ['sku', 'código', 'id', 'identificador', 'material', 'item', 'referencia'],
            'costo': ['costo', 'cost', 'precio', 'valor', 'importe', 'monto']
        }

        logic_triggers = [
            'calcular', 'considerar', 'equivale', 'representa', 'es la suma', 'restar', 'multiplicar', 'dividir',
            '+', '-', '*', '/', '%', '>', '<', '=',
            'donde', 'cuando', 'si ', 'entonces'
        ]

        for item in response.data:
            term_raw = str(item['term']).strip()
            definition_raw = str(item['definition']).strip()
            definition_lower = definition_raw.lower()

            is_complex_rule = any(trigger in definition_lower for trigger in logic_triggers)

            if is_complex_rule:
                glossary_map[term_raw] = definition_raw
                continue

            mapped_col = None
            for technical_name, keywords in target_concepts.items():
                if any(kw in definition_lower for kw in keywords):
                    mapped_col = technical_name
                    break

            if mapped_col:
                term_clean = term_raw.lower().replace(' ', '_').replace('/', '_').replace('.', '')
                glossary_map[term_clean] = mapped_col
            else:
                glossary_map[term_raw] = definition_raw

        return glossary_map

    except Exception as e:
        return {}


def detect_data_dna(df: pd.DataFrame) -> dict:
    cols = [str(c).lower().strip() for c in df.columns]
    money_terms = ['precio', 'costo', 'venta', 'revenue', 'monto', 'importe', 's/.', '$', 'usd', 'price', 'cost']
    risk_terms = ['vencimiento', 'caducidad', 'expiry', 'fecaduc', 'fecha_venc']
    id_terms = ['id', 'cod', 'sku', 'ean', 'lote', 'batch', 'dni', 'ruc', 'order', 'material']

    has_money = any(any(t in c for t in money_terms) for c in cols)
    has_risk = any(any(t in c for t in risk_terms) for c in cols)

    forbidden_sums = [c for c in df.columns if any(t in str(c).lower() for t in id_terms) and pd.api.types.is_numeric_dtype(df[c])]

    return {
        "ADN_FINANCIERO": has_money,
        "ADN_RIESGO": has_risk,
        "COLUMNAS_PROHIBIDAS_SUMA": forbidden_sums,
        "COLUMNAS_DETECTADAS": list(df.columns)
    }


def forecast_series(df: pd.DataFrame, date_col: str, value_col: str, horizon_months: int = 3) -> list:
    try:
        if df.empty or len(df) < 4: return [{"error": "Datos insuficientes para forecast (min 4 periodos)."}]
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        df = df.sort_values(date_col)
        freq = 'M' if horizon_months > 2 else 'W'
        series = df.set_index(date_col)[value_col].resample(freq).sum().fillna(0)
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        model = ExponentialSmoothing(series, trend='add', seasonal='add', seasonal_periods=4, damped_trend=True).fit()
        forecast = model.forecast(horizon_months)
        history = series.reset_index(); history.columns = ['fecha', 'valor']; history['tipo'] = 'Histórico'
        future = forecast.reset_index(); future.columns = ['fecha', 'valor']; future['tipo'] = 'Proyección'
        result = pd.concat([history, future]).sort_values('fecha')
        result['fecha'] = result['fecha'].dt.strftime('%Y-%m-%d')
        return result.to_dict(orient='records')
    except Exception as e: return [{"error": f"Error Forecast: {str(e)}"}]


def detect_anomalies(df: pd.DataFrame, value_col: str, contamination: float = 0.05) -> list:
    try:
        from app.services.predictive_engine import PredictiveEngine
        result_df = PredictiveEngine.detect_anomalies(df, value_col, contamination=contamination)
        if 'is_anomaly' in result_df.columns:
            anomalies = result_df[result_df['is_anomaly']]
            return anomalies.nlargest(50, value_col).to_dict(orient='records')
        return []
    except Exception as e: return [{"error": str(e)}]


def analyze_key_drivers(df: pd.DataFrame, target_col: str) -> list:
    try:
        from sklearn.ensemble import RandomForestRegressor
        df_clean = df.copy().dropna(subset=[target_col])
        numeric_cols = df_clean.select_dtypes(include=[np.number]).columns.drop(target_col, errors='ignore').tolist()
        if not numeric_cols: return []
        X = df_clean[numeric_cols].fillna(0); y = df_clean[target_col]
        rf = RandomForestRegressor(n_estimators=50, max_depth=5); rf.fit(X, y)
        imps = pd.DataFrame({'feature': numeric_cols, 'importance': rf.feature_importances_})
        return imps.sort_values('importance', ascending=False).head(5).to_dict(orient='records')
    except Exception as e: return [{"error": str(e)}]


def detect_header_row(df_raw: pd.DataFrame) -> int:
    """Detecta heurísticamente la fila de cabecera real en Excels sucios."""
    for i in range(min(10, len(df_raw))):
        row_valid_count = df_raw.iloc[i].dropna().astype(str).map(len).gt(1).sum()
        if row_valid_count > 1: return i
    return 0


def preprocess_dataframe(df: pd.DataFrame, dynamic_glossary: dict = None) -> pd.DataFrame:
    """
    Protocolo de Limpieza V5 (Inteligente, Dinámico y Blindado).
    Integra Glosario del Usuario + Detección Automática de Patrones + Compatibilidad Total.
    """
    if dynamic_glossary is None:
        dynamic_glossary = {}

    df.columns = [
        str(c).strip().lower()
        .replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
        .replace('/', '_').replace('-', '_').replace('.', '').replace(' ', '_') \
        .replace('(', '').replace(')', '') \
        .replace('$', '').replace('%', '')
        for c in df.columns
    ]

    semantic_map = {
        'feprefercons': 'fecha_vencimiento', 'fecaduc': 'fecha_vencimiento',
        'fe_caduc': 'fecha_vencimiento', 'expiry_date': 'fecha_vencimiento',
        'vencimiento': 'fecha_vencimiento',
        'ubicacion': 'ubicacion', 'almacen': 'almacen', 'warehouse': 'almacen', 'tienda': 'almacen',
        'texto_breve_de_material': 'descripcion', 'material': 'sku', 'item': 'sku', 'codigo': 'sku',
        'stock_disponible': 'stock', 'libre_utilizacion': 'stock', 'qty': 'stock', 'on_hand': 'stock', 'cantidad': 'stock',
        'region_country_name': 'pais', 'country': 'pais', 'territory': 'pais',
        'fecaduc_feprefercons': 'fecha_vencimiento',
        'fecha_de_stock': 'fecha', 'dia': 'fecha'
    }

    semantic_map.update(dynamic_glossary)

    new_cols = {k: v for k, v in semantic_map.items() if k in df.columns}
    df = df.rename(columns=new_cols)

    id_keywords = ['id', 'sku', 'cod', 'dni', 'ruc', 'lote', 'batch', 'material']
    date_keywords = ['fecha', 'date', 'time', 'periodo', 'vencimiento', 'caducidad', 'fec']
    num_keywords = ['stock', 'cantidad', 'valor', 'precio', 'costo', 'peso', 'altura', 'variacion', 'balance', 'importe', 'monto', 'total']

    for col in df.columns:
        col_str = str(col).lower()
        df[col] = df[col].replace([r'^#.*!', r'^#N/A', 'nan', 'NaN', 'null'], np.nan, regex=True)

        if any(k in col_str for k in id_keywords):
            df[col] = df[col].astype(str).str.strip().replace('nan', '')
            continue

        is_date_name = any(k in col_str for k in date_keywords)
        is_date_content = False

        if df[col].dtype == 'object' and not is_date_name:
            sample = df[col].dropna().head(10).astype(str)
            if sample.str.match(r'(\d{4}-\d{2}-\d{2})|(\d{2}/\d{2}/\d{4})').sum() > 5:
                is_date_content = True

        if is_date_name or is_date_content:
            df[col] = pd.to_datetime(df[col], errors='coerce')
            continue

        if df[col].dtype == 'object' and not any(k in col_str for k in num_keywords):
            df[col] = df[col].astype(str).str.strip().str.title().replace('Nan', '')

        is_semantic_num = any(k in col_str for k in num_keywords)

        if is_semantic_num or df[col].dtype == 'object':
            if df[col].dtype == 'object':
                clean_col = df[col].astype(str).str.replace(r'\s+[-]\s+', ' ', regex=True)
                clean_col = clean_col.str.replace(r'[^\d.,-]', '', regex=True)

                def clean_regional(val: Any) -> Any:
                    if not val: return val
                    if ',' in val and '.' in val:
                        if val.rfind(',') > val.rfind('.'): return val.replace('.', '').replace(',', '.')
                        else: return val.replace(',', '')
                    elif ',' in val: return val.replace(',', '.')
                    return val

                df[col] = pd.to_numeric(clean_col.apply(clean_regional), errors='coerce')
            else:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            if 'stock' in col_str or 'cantidad' in col_str:
                df[col] = df[col].fillna(0)

    return df


def get_dataframe_from_storage(supabase: Any, file_id: str, glossary_map: dict = None) -> tuple[dict, list]:
    """
    Lee el archivo crudo del Storage.
    NOTA: Ya NO limpiamos aquí. La limpieza la hará el DataEngine en el siguiente paso.
    """
    if glossary_map is None:
        glossary_map = {}

    resp = supabase.table('uploaded_files').select('storage_path').eq('id', file_id).single().execute()
    file_bytes = supabase.storage.from_('dash-uploads').download(resp.data['storage_path'])
    f_io = io.BytesIO(file_bytes)

    audit_log = []
    dfs = {}

    try:
        try:
            xls = pd.ExcelFile(f_io)
            for sheet in xls.sheet_names:
                df_sheet = pd.read_excel(xls, sheet_name=sheet)
                dfs[sheet] = df_sheet
        except Exception:
            f_io.seek(0)
            df = pd.read_csv(f_io, encoding='latin-1', on_bad_lines='skip')
            dfs['principal'] = df

        return dfs, audit_log
    except Exception as e:
        raise Exception(f"Error crítico leyendo archivo raw: {str(e)}")


def load_dataset_for_task(supabase: Any, file_id: str, user_id: str | None, prompt: str, user_token: str, glossary_map: dict = None) -> tuple:
    """Wrapper que orquesta carga de datos, DNA detection y preprocessing."""
    if glossary_map is None:
        glossary_map = {}
    actual_prompt = clean_business_terms(prompt)
    dfs, audit_log = get_dataframe_from_storage(
        supabase, file_id, glossary_map
    )
    main_df = dfs.get("df") or next(iter(dfs.values())) if dfs else None
    adn = detect_data_dna(main_df) if main_df is not None else {}
    return dfs, actual_prompt, adn, {}, audit_log, {}, {}
