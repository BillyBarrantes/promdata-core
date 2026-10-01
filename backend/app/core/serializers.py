import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd


def convert_keys_to_str(obj):
    if isinstance(obj, dict):
        return {str(k): convert_keys_to_str(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_keys_to_str(i) for i in obj]
    return obj

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (pd.Period, pd.Timestamp)): 
            return str(obj)
        if isinstance(obj, (np.integer, np.int64)): 
            return int(obj)
        if isinstance(obj, pd.Series): 
            return convert_keys_to_str(obj.to_dict())
        if isinstance(obj, pd.DataFrame): 
             try:
                 # Límite duro para evitar tablas gigantes en el JSON final, 
                 # ajustado para permitir análisis sin saturar memoria.
                 limit = 2000 if len(obj) > 2000 else len(obj)
                 return convert_keys_to_str(obj.head(limit).to_dict(orient='records'))
             except Exception as _df_err:
                 print(f"Warn: DataFrame->JSON falló ({type(_df_err).__name__}); usando DF_Error")
                 return "DF_Error"
        return super().default(obj)


def json_safe(obj):
    """Copia de ``obj`` con floats no finitos (NaN/Infinity) convertidos a ``None``.

    El estándar JSON no admite ``NaN``/``Infinity`` y Starlette serializa las
    respuestas con ``allow_nan=False``. Un único valor no finito en el payload
    provoca ``ValueError`` → HTTP 500 ilegible para el navegador (sin headers
    CORS, reportado como ``TypeError: Failed to fetch``).

    Es una extensión aditiva: no reemplaza a ``CustomEncoder`` (que solo cubre
    tipos desconocidos), sino que normaliza el árbol antes de ``json.dumps``.
    Aplicada en la frontera de persistencia y de lectura para que ningún NaN
    alcance la respuesta HTTP.
    """
    if obj is None or obj is pd.NaT or obj is pd.NA:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, np.generic):
        return json_safe(obj.item())
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(key): json_safe(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(item) for item in obj]
    if isinstance(obj, pd.Series):
        return json_safe(obj.tolist())
    if isinstance(obj, pd.DataFrame):
        limit = 2000 if len(obj) > 2000 else len(obj)
        return json_safe(convert_keys_to_str(obj.head(limit).to_dict(orient="records")))
    return obj


def dumps_safe(obj, **kwargs) -> str:
    """``json.dumps`` estricto: sanea no finitos y prohíbe ``NaN``/``Infinity``.

    Si algún valor no finito sobrevive al saneamiento, la serialización falla
    en el punto de persistencia (visible en el worker) en lugar de propagar un
    payload inválido que romperá la API al leerlo.
    """
    kwargs.setdefault("cls", CustomEncoder)
    return json.dumps(json_safe(obj), allow_nan=False, **kwargs)


def has_non_finite(obj) -> bool:
    """Detecta presencia de ``NaN``/``Infinity`` en un árbol JSON-like.

    Observabilidad: permite registrar cuándo un payload legacy fue saneado en
    la frontera de lectura, sin necesidad de inspeccionarlo manualmente.
    """
    if obj is None or isinstance(obj, bool):
        return False
    if isinstance(obj, np.generic):
        return has_non_finite(obj.item())
    if isinstance(obj, float):
        return not math.isfinite(obj)
    if isinstance(obj, dict):
        return any(has_non_finite(value) for value in obj.values())
    if isinstance(obj, (list, tuple, set)):
        return any(has_non_finite(item) for item in obj)
    if isinstance(obj, pd.Series):
        return has_non_finite(obj.tolist())
    if isinstance(obj, pd.DataFrame):
        return has_non_finite(obj.to_dict(orient="records"))
    return False
