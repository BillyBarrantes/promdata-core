# En: backend/app/services/smart_table_builder.py
"""
Smart Table Builder V1.0 — Semáforo de Densidad & Convertidor ECharts→SmartTable.

Cuando un gráfico ECharts tiene >20 categorías en el eje X, este módulo
convierte la estructura ECharts en un payload SmartTable empresarial con
Data Bars, Heatmap visual y metadatos de formato.

Principios:
- Schema-Agnostic: No hardcodea nombres de columnas.
- Extension-Only: Nuevo módulo, no modifica nada existente.
- Type Hints: Todos los parámetros y retornos tipados.
"""

from typing import Any, Optional
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# CONSTANTES
# ---------------------------------------------------------------------------
DENSITY_THRESHOLD: int = 20  # Categorías mínimas para activar Smart Table
ROWS_PER_PAGE: int = 25

# ---------------------------------------------------------------------------
# SEMÁFORO DE DENSIDAD
# ---------------------------------------------------------------------------

def should_use_smart_table(chart_option: dict[str, Any]) -> bool:
    """
    Evalúa si un gráfico ECharts debería renderizarse como Smart Table.

    Regla: Si el eje X categórico tiene más de DENSITY_THRESHOLD categorías,
    la visualización es demasiado densa para un gráfico y se beneficia de
    una tabla empresarial.

    Args:
        chart_option: Diccionario ECharts option completo.

    Returns:
        True si debe mostrarse como Smart Table, False si el gráfico es adecuado.
    """
    x_axis = chart_option.get('xAxis')
    if not x_axis:
        return False

    # Normalizar: xAxis puede ser dict o list
    if isinstance(x_axis, list):
        x_axis = x_axis[0] if x_axis else {}

    # Solo aplica a ejes categóricos con datos explícitos
    if x_axis.get('type') != 'category':
        return False

    categories = x_axis.get('data', [])
    return len(categories) >= DENSITY_THRESHOLD


def _looks_temporal_label(raw_value: Any) -> bool:
    """
    Detecta labels temporales comunes sin hardcodear columnas.
    """
    if raw_value is None:
        return False

    label = str(raw_value).strip()
    if not label:
        return False

    normalized = re.sub(r'\s+', ' ', label)

    simple_patterns = (
        r'^\d{4}-\d{2}-\d{2}$',
        r'^\d{4}/\d{2}/\d{2}$',
        r'^\d{2}/\d{2}/\d{4}$',
        r'^\d{2}-\d{2}-\d{4}$',
        r'^\d{6}$',
        r'^\d{8}$',
        r'^[A-Za-z]{3,9}[-\s]\d{4}$',
    )
    if any(re.match(pattern, normalized) for pattern in simple_patterns):
        return True

    month_name_pattern = (
        r'^(?:ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic|'
        r'jan|apr|aug|dec|'
        r'enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|'
        r'octubre|noviembre|diciembre|'
        r'january|february|march|april|may|june|july|august|september|'
        r'october|november|december)'
        r'[-\s]\d{4}$'
    )
    if re.match(month_name_pattern, normalized, flags=re.IGNORECASE):
        return True

    date_formats = (
        '%Y-%m-%d',
        '%Y/%m/%d',
        '%d/%m/%Y',
        '%d-%m-%Y',
        '%b-%Y',
        '%b %Y',
        '%B-%Y',
        '%B %Y',
        '%Y%m',
        '%Y%m%d',
    )
    for date_format in date_formats:
        try:
            datetime.strptime(normalized, date_format)
            return True
        except ValueError:
            continue

    return False


def should_offer_hybrid_smart_table(chart_option: dict[str, Any]) -> bool:
    """
    Activa vista híbrida gráfico↔tabla para series temporales.

    No reemplaza el semáforo de densidad; solo extiende el contrato para
    tendencias donde el usuario necesita alternar entre gráfico y tabla.
    """
    x_axis = chart_option.get('xAxis')
    if not x_axis:
        return False

    if isinstance(x_axis, list):
        x_axis = x_axis[0] if x_axis else {}

    categories = x_axis.get('data', []) or []
    if x_axis.get('type') != 'category' or len(categories) < 3:
        return False

    non_empty_categories = [category for category in categories if str(category).strip()]
    if len(non_empty_categories) < 3:
        return False

    sample = non_empty_categories[: min(6, len(non_empty_categories))]
    temporal_hits = sum(1 for label in sample if _looks_temporal_label(label))
    if temporal_hits / len(sample) < 0.6:
        return False

    series_list = chart_option.get('series', []) or []
    if not isinstance(series_list, list) or not series_list:
        return False

    has_line_series = any(series.get('type') == 'line' for series in series_list if isinstance(series, dict))
    return has_line_series


# ---------------------------------------------------------------------------
# CONVERTIDOR ECHARTS → SMART TABLE
# ---------------------------------------------------------------------------

def _detect_column_type(series_name: str) -> str:
    """
    Detecta el tipo de columna basándose en el nombre de la serie.
    Data-driven: busca patrones comunes de porcentaje y variación.

    Args:
        series_name: Nombre de la serie ECharts.

    Returns:
        'percentage' si parece porcentaje, 'number' si no.
    """
    name_lower = series_name.lower()
    percentage_indicators = ['%', 'variaci', 'porcentaje', 'tasa', 'margen', 'ratio', 'pct']
    if any(indicator in name_lower for indicator in percentage_indicators):
        return 'percentage'
    return 'number'


def _coerce_numeric_value(raw_value: Any) -> Optional[float]:
    """
    Convierte un valor heterogéneo ECharts a float seguro.
    Soporta formato directo o dict {'value': ...}.
    """
    if isinstance(raw_value, dict):
        raw_value = raw_value.get('value', None)

    if isinstance(raw_value, bool):
        return None

    if isinstance(raw_value, (int, float)):
        return float(raw_value)

    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


def _build_row_sparkline(series_list: list[dict[str, Any]], row_index: int, window_size: int = 12) -> Optional[list[float]]:
    """
    Genera una serie sparkline por fila:
    - Multi-serie: valores de todas las series en la categoría de la fila.
    - Serie única: ventana temporal alrededor de la fila.
    """
    if not series_list:
        return None

    # Caso A: varias series -> perfil por categoría
    if len(series_list) > 1:
        values: list[float] = []
        for series in series_list:
            s_data = series.get('data', [])
            if row_index >= len(s_data):
                continue
            numeric = _coerce_numeric_value(s_data[row_index])
            if numeric is not None:
                values.append(numeric)
        return values if len(values) >= 2 else None

    # Caso B: serie única -> ventana histórica
    only_series_data = series_list[0].get('data', [])
    numeric_series: list[float] = []
    for point in only_series_data:
        numeric = _coerce_numeric_value(point)
        if numeric is not None:
            numeric_series.append(numeric)

    if len(numeric_series) < 2:
        return None

    # Ventana preferente: últimos N hasta la fila
    start = max(0, row_index - window_size + 1)
    end = min(len(numeric_series), row_index + 1)
    segment = numeric_series[start:end]

    # Fallback: ventana centrada si la preferente quedó muy corta
    if len(segment) < 2:
        half = window_size // 2
        start = max(0, row_index - half)
        end = min(len(numeric_series), row_index + half + 1)
        segment = numeric_series[start:end]

    return segment if len(segment) >= 2 else None


def echarts_to_smart_table(
    chart_option: dict[str, Any],
    title: str,
    sort_order: str = 'desc',
    default_view_mode: str = 'table',
) -> dict[str, Any]:
    """
    Convierte un ECharts option con muchas categorías en un payload Smart Table.

    Extrae:
    - Categorías del xAxis → columna "dimension" (tipo texto)
    - Cada serie → una columna numérica con metadatos de formato
    - Determina sort_by automáticamente (primera serie numérica)

    Args:
        chart_option: Diccionario ECharts option completo.
        title: Título del análisis para la tabla.
        sort_order: Orden inicial ('asc' o 'desc').

    Returns:
        Diccionario con estructura smart_table completa, incluyendo
        el chart_option original para el toggle vista.
    """
    x_axis = chart_option.get('xAxis', {})
    if isinstance(x_axis, list):
        x_axis = x_axis[0] if x_axis else {}

    categories: list[str] = x_axis.get('data', [])
    series_list: list[dict] = chart_option.get('series', [])

    # --- Construir definición de columnas ---
    columns: list[dict[str, Any]] = []

    # Columna primaria: la dimensión del eje X
    dim_label = x_axis.get('name', '')
    if not dim_label:
        # Intentar extraer del title del chart como heurística
        title_obj = chart_option.get('title', {})
        if isinstance(title_obj, list) and title_obj:
            title_obj = title_obj[0]
        if isinstance(title_obj, dict):
            raw_title = title_obj.get('text', '')
            if raw_title:
                # Limpiar prefijos preposicionales comunes del título
                # Ej: "Stock Por Producto" → "Producto", "Ventas De Almacén" → "Almacén"
                cleaned = re.sub(
                    r'^.*?\b(?:por|de|según|segun|by|per)\b\s+',
                    '',
                    raw_title,
                    flags=re.IGNORECASE
                ).strip()
                dim_label = cleaned if cleaned else raw_title
    # Fallback final: genérico seguro
    if not dim_label:
        dim_label = 'Categoría'
    columns.append({
        "key": "dimension",
        "label": dim_label,
        "type": "text"
    })

    # Una columna por serie numérica
    sort_by_key: Optional[str] = None
    for idx, series in enumerate(series_list):
        series_name = series.get('name', f'Valor {idx + 1}')
        col_key = f"serie_{idx}"
        col_type = _detect_column_type(series_name)

        col_def: dict[str, Any] = {
            "key": col_key,
            "label": series_name,
            "type": col_type,
        }

        # Data Bars para números, Heatmap para porcentajes
        if col_type == 'number':
            col_def["bar"] = True
            if sort_by_key is None:
                sort_by_key = col_key  # Primera columna numérica como sort default
        elif col_type == 'percentage':
            col_def["heatmap"] = True

        columns.append(col_def)

    # --- Construir filas de datos ---
    data: list[dict[str, Any]] = []
    has_sparkline = False
    for i, cat in enumerate(categories):
        row: dict[str, Any] = {"dimension": cat}
        for idx, series in enumerate(series_list):
            col_key = f"serie_{idx}"
            series_data = series.get('data', [])
            raw_value = series_data[i] if i < len(series_data) else None

            # ECharts data puede ser valor directo o {value: X, ...}
            if isinstance(raw_value, dict):
                raw_value = raw_value.get('value', None)

            row[col_key] = raw_value

        sparkline_data = _build_row_sparkline(series_list, i)
        if sparkline_data:
            row["sparkline_data"] = sparkline_data
            has_sparkline = True
        data.append(row)

    # Si no encontramos sort_by, usar la primera columna no-texto
    if sort_by_key is None and len(columns) > 1:
        sort_by_key = columns[1]["key"]

    if has_sparkline:
        columns.append({
            "key": "sparkline_data",
            "label": "Tendencia",
            "type": "sparkline"
        })

    return {
        "type": "smart_table",
        "title": title,
        "columns": columns,
        "data": data,
        "sort_by": sort_by_key or "dimension",
        "sort_order": sort_order,
        "original_chart_option": chart_option,
        "default_view_mode": default_view_mode,
        "row_count": len(data),
        "page_size": ROWS_PER_PAGE
    }
