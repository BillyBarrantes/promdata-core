import json
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
             except: 
                 return "DF_Error"
        return super().default(obj)
