# En: backend/app/core/semantic_grammar.py
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Union, Literal
from enum import Enum

# --- 1. VOCABULARIO BÁSICO (Atomic Types) ---

class MetricUnit(str, Enum):
    CURRENCY = "currency"   # Dinero (S/, $)
    QUANTITY = "quantity"   # Unidades, Piezas, Stock
    PERCENTAGE = "percentage" # Margenes, Tasas, %
    NUMBER = "number"       # Neutro

class MetricPolarity(str, Enum):
    """Polaridad de negocio: ¿quieres que suba o baje esta métrica?"""
    FAVORABLE = "favorable"       # Quieres MÁS (ventas, ingresos, producción, satisfacción)
    UNFAVORABLE = "unfavorable"   # Quieres MENOS (vencimientos, merma, errores, deudas, quejas)
    NEUTRAL = "neutral"           # Sin dirección preferida (stock general, conteo descriptivo)

class TimeGrain(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"

class FilterOperator(str, Enum):
    """Operadores de filtro soportados por IbisEngine.
    
    [V2] Ampliado con operadores de texto y matemáticos completos para cubrir
    todos los valores que el LLM (Gemini) puede generar en contratos semánticos.
    El validator 'normalize_operator' en DataFilter mapea todos los alias posibles
    a estos valores canónicos antes de la ejecución.
    """
    # --- Operadores de igualdad ---
    EQUALS = "=="
    NOT_EQUALS = "!="

    # --- Operadores matemáticos de rango ---
    GREATER_THAN = ">"
    LESS_THAN = "<"
    GREATER_EQUAL = ">="   # [FASE 3F] Necesario para filtros de rango (ej: fecha >= X)
    LESS_EQUAL = "<="       # [FASE 3F] Necesario para filtros de rango (ej: fecha <= X)

    # --- Operadores de texto (case-insensitive en IbisEngine) ---
    CONTAINS = "contains"           # Substring match: col.upper().contains(val.upper())
    ILIKE = "ilike"                 # [V2] Alias SQL para contains case-insensitive
    LIKE = "like"                   # [V2] Pattern match (mapeado a contains en Ibis)
    STARTS_WITH = "starts_with"     # [V2] col.upper().startswith(val.upper())
    ENDS_WITH = "ends_with"         # [V2] col.upper().endswith(val.upper())
    NOT_CONTAINS = "not_contains"   # [V2] ~col.upper().contains(val.upper())
    NOT_LIKE = "not_like"           # [V2] Alias de not_contains

    # --- Operadores de conjunto ---
    IN_LIST = "in"
    NOT_IN_LIST = "not_in"

class VisualProtocol(str, Enum):
    """El Arsenal Gráfico (Basado en PDF: Gráficos Empresariales)"""
    BAR = "bar_chart"           # Comparación Estructural
    LINE = "line_chart"         # Evolutivo Simple
    AREA = "area_chart"         # Evolutivo Acumulado
    PIE = "pie_chart"           # Parte de un todo (< 5 items)
    SCATTER = "scatter_plot"    # Relacional (Correlación)
    HISTOGRAM = "histogram"     # Distributivo (Frecuencia)

    HEATMAP = "heatmap"         # Relacional (Intensidad)
    WATERFALL = "waterfall"     # Financiero (Flujo)
    TREEMAP = "treemap"         # Jerárquico / Densidad
    FUNNEL = "funnel_chart"     # Conversión / Proceso
    BOXPLOT = "boxplot"         # Variabilidad / Outliers
    KPI = "kpi_card"            # Dato único
    DUAL_AXIS = "dual_axis_chart"  # Combinado: Barras + Línea (2 métricas, 2 escalas)

class DataFilter(BaseModel):
    """Representa un filtro SQL seguro: WHERE columna op valor"""
    column: str = Field(..., description="Nombre técnico de la columna (ej: 'fecha_vencimiento')")
    operator: FilterOperator = Field(..., description="Operador lógico")
    value: Union[str, int, float, List[Union[str, int, float]]] = Field(..., description="Valor a filtrar")

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, value):
        """[V2] Normaliza todos los alias de operadores que el LLM puede emitir
        al valor canónico del FilterOperator enum.
        
        Cubre: SQL, Python, inglés/español, formatos abreviados y la forma
        de un solo '=' que Gemini emite en el fastpath SIMPLE.
        Si el alias es desconocido, lo devuelve tal cual para que el enum
        valide (y falle explícitamente si no es válido).
        """
        normalized = str(getattr(value, "value", value) or "").strip().lower()
        aliases = {
            # --- Igualdad ---
            "=": "==",
            "equals": "==",
            "equal": "==",
            "eq": "==",
            "es": "==",
            "igual": "==",
            "igual_a": "==",
            # --- Desigualdad ---
            "not_equals": "!=",
            "not equals": "!=",
            "neq": "!=",
            "<>": "!=",
            "ne": "!=",
            "distinto": "!=",
            "diferente": "!=",
            # --- Rango numérico ---
            "gt": ">",
            "greater": ">",
            "mayor": ">",
            "mayor_que": ">",
            "lt": "<",
            "less": "<",
            "menor": "<",
            "menor_que": "<",
            "gte": ">=",
            "greater_equal": ">=",
            "mayor_igual": ">=",
            "mayor_o_igual": ">=",
            "lte": "<=",
            "less_equal": "<=",
            "menor_igual": "<=",
            "menor_o_igual": "<=",
            # --- Texto / Pattern matching ---
            "ilike": "ilike",
            "like": "like",
            "contains": "contains",
            "contiene": "contains",
            "incluye": "contains",
            "include": "contains",
            "match": "contains",
            "starts_with": "starts_with",
            "startswith": "starts_with",
            "empieza_con": "starts_with",
            "inicia_con": "starts_with",
            "ends_with": "ends_with",
            "endswith": "ends_with",
            "termina_con": "ends_with",
            "not_contains": "not_contains",
            "not contains": "not_contains",
            "no_contiene": "not_contains",
            "not_like": "not_like",
            "not like": "not_like",
            "no_like": "not_like",
            # --- Conjunto ---
            "in_list": "in",
            "in_values": "in",
            "in": "in",
            "not in": "not_in",
            "not_in": "not_in",
            "not_in_list": "not_in",
            "not_in_values": "not_in",
            "nin": "not_in",
        }
        return aliases.get(normalized, normalized)

# --- 2. INTENCIONES (Familias de Pensamiento) ---

class BaseIntent(BaseModel):
    """Clase padre para todas las intenciones analíticas"""
    rationale: str = Field(..., description="Explicación breve de por qué se eligió esta operación")
    filters: List[DataFilter] = Field(default_factory=list, description="Filtros globales a aplicar antes del cálculo")
    metric_unit: Optional[MetricUnit] = Field(
        default=None,
        description="Unidad semántica de la métrica principal. Se usa para blindar narrativa y formato visual."
    )

    # 🟩 [NUEVO] El Cerebro decide el gráfico basándose en la Topología del dato
    visual_protocol: Optional[VisualProtocol] = Field(
        default=None, 
        description="El gráfico idóneo según la naturaleza matemática (Ej: Flujo -> WATERFALL, Tiempo -> LINE)"
    )
    negative_filters: List[DataFilter] = Field(
        default_factory=list,
        description="Exclusiones explícitas a aplicar antes del cálculo. Ej: categoria NOT IN ['Software']."
    )
    plot_metric: Optional[str] = Field(
        default=None,
        description="Métrica que se grafica o reporta. Si no se especifica, usa la métrica principal del intent."
    )
    ranking_metric: Optional[str] = Field(
        default=None,
        description="Métrica independiente usada para ordenar/rankear Top N antes de graficar plot_metric."
    )
    ranking_direction: Literal["desc", "asc"] = Field(
        default="desc",
        description="Dirección del ranking cuando ranking_metric está presente."
    )

class DescriptiveIntent(BaseIntent):
    """Para KPIs puntuales y agregaciones simples (ej: 'Total de ventas', 'Stock promedio')"""
    type: Literal["descriptive"] = "descriptive"
    metrics: List[str] = Field(..., description="Columnas numéricas a agregar (ej: ['importe', 'cantidad'])")

    # [NUEVO] Campo OBLIGATORIO para que el LLM decida la unidad basada en la metadata
    metric_unit: MetricUnit = Field(
        default=MetricUnit.CURRENCY, 
        description="Tipo de unidad de la métrica principal. Si la columna dice 'UNIT: QUANTITY', usa 'quantity'."
    )

    aggregation: Literal["sum", "avg", "min", "max", "count"] = "sum"
    group_by: Optional[List[str]] = Field(None, description="Columnas para agrupar (ej: ['categoria'])")

class TimeTrendIntent(BaseIntent):
    """Para análisis de evolución temporal (ej: 'Evolución de precios mensual')

    V6.5: Added split_dimension/split_limit for multi-series trend charts
    (e.g., 'Evolución mensual comparando top 5 productos').
    """
    type: Literal["trend"] = "trend"
    date_column: str = Field(..., description="Columna de fecha principal")
    value_column: str = Field(..., description="Métrica a analizar en el tiempo")
    grain: TimeGrain = Field(default=TimeGrain.MONTH, description="Granularidad temporal")
    fill_missing: bool = Field(default=True, description="Si rellenar huecos temporales con 0")
    # V6.5: Optional dimensional split for multi-series line charts
    split_dimension: Optional[str] = Field(default=None, description="Columna categórica para generar una serie por categoría (ej: 'producto')")
    split_limit: Optional[int] = Field(default=None, description="Top N categorías a incluir como series (ej: 5 para Top 5)")
    # V6.6: Top-N aggregation behavior.
    # - split: una serie por categoría Top-N (comportamiento clásico)
    # - sum: suma de categorías Top-N en una sola serie temporal
    top_n_aggregation_mode: Literal["split", "sum"] = Field(
        default="split",
        description="Modo de agregación para tendencias Top-N: 'split' separa series; 'sum' agrupa Top-N en una sola línea."
    )

class DistributionIntent(BaseIntent):
    """Para entender cómo se reparten los datos (ej: 'Top 10 productos', 'Pareto', 'Histograma', 'Desglose')"""
    type: Literal["distribution"] = "distribution"
    dimension: str = Field(..., description="Categoría principal (ej: 'sku', 'almacen')")
    metric: str = Field(..., description="Métrica para ordenar/pesar (ej: 'stock')")
    limit: Optional[int] = Field(10, description="Límite de resultados (Top N) sobre la dimensión principal")
    group_by: Optional[List[str]] = Field(None, description="Columnas secundarias para desglosar/apilar el análisis principal (ej: ['tipo_almacen'])")
    barmode: Literal["stacked", "group"] = Field(default="stacked", description="Si se especifica group_by, usa 'stacked' para barras apiladas (ej: 'apilado', 'apilar') y 'group' para barras agrupadas/lado a lado (ej: 'lado a lado', 'compara').")

# --- 2.1 NEW INTENT TYPES (Schema-Agnostic V7) ---

class DiagnosticIntent(BaseIntent):
    """Para responder '¿Por qué pasó?' — Boxplot, Scatter, Funnel, Correlaciones"""
    type: Literal["diagnostic"] = "diagnostic"
    metric: Optional[str] = Field(None, description="Columna métrica principal a analizar")
    metrics: List[str] = Field(default_factory=list, description="Múltiples métricas (para scatter/correlación)")
    dimension: Optional[str] = Field(None, description="Columna categórica para agrupar")
    group_by: Optional[List[str]] = Field(None, description="Columnas para agrupar")
    aggregation: Literal["sum", "avg", "min", "max", "count"] = "sum"

class PredictiveIntent(BaseIntent):
    """Para responder '¿Qué pasará?' — Forecast, Anomalías, Proyecciones"""
    type: Literal["predictive"] = "predictive"
    date_column: str = Field(..., description="Columna de fecha para el eje temporal")
    value_column: str = Field(..., description="Métrica a proyectar")
    metric: Optional[str] = Field(None, description="Alias alternativo para value_column")
    analysis_subtype: Literal["forecast", "anomalies", "trend_projection"] = Field(
        default="forecast", description="Tipo de análisis predictivo"
    )
    grain: TimeGrain = Field(default=TimeGrain.MONTH, description="Granularidad temporal")
    horizon: int = Field(default=6, description="Períodos a proyectar hacia el futuro")

# --- 3. CONTENEDOR MAESTRO (El JSON final que devolverá la IA) ---

class AnalysisPlan(BaseModel):
    """El plan maestro que orquesta la ejecución en Ibis"""
    main_intent: Union[
        DescriptiveIntent, TimeTrendIntent, DistributionIntent,
        DiagnosticIntent, PredictiveIntent
    ] = Field(
        ..., description="La intención principal detectada"
    )
    title: str = Field(..., description="Título sugerido para el gráfico/tabla")

    # 🟩 [NUEVO] Inferencia Semántica On-The-Fly
    # Gemini llenará esto: { "totalRevenue": "Ingresos Totales", "cod_alm": "Almacén" }
    column_aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Diccionario que traduce nombres técnicos a Negocio (Ej: {'total_vta': 'Ventas Totales'}). ÚSALO SIEMPRE."
    )

    # 🧭 [FASE 3D] Polaridad de Métrica — Contexto semántico macro
    metric_polarity: Optional[MetricPolarity] = Field(
        default=MetricPolarity.NEUTRAL,
        description="Polaridad de la métrica según contexto del negocio. 'favorable'=se quiere maximizar (ventas, ingresos). 'unfavorable'=se quiere minimizar (vencimientos, merma, errores). 'neutral'=informativo."
    )

    # 🛡️ [V8] Anti-Hallucination: If the AI can't map a concept to a column,
    # it sets this hint so the system can suggest the user update the Glossary
    glossary_hint: Optional[str] = Field(
        default=None,
        description="Si no puedes mapear un concepto del usuario a una columna, escribe aquí qué término falta en el glosario. Ej: 'No encontré una columna de vencimiento. Sugiero agregar al Glosario qué columna contiene fechas de caducidad.'"
    )
