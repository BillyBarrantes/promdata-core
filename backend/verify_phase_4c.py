
import pandas as pd
import re
from app.services.data_engine import DataEngine
from app.services.semantic_translator import SemanticTranslator

def title(t):
    print(f"\n{'='*50}\n {t}\n{'='*50}")

def verify_strict_id_casting():
    title("VERIFICACIÓN 1: Strict ID Casting (Data Engine)")
    
    # Simular DataFrame con IDs numéricos de BAJA cardinalidad (El caso 'Tipo Almacén')
    # store_id: 100, 200, 300 (enteros)
    # warehouse_code: 810.0, 130.0 (flotantes que parecen IDs)
    data = {
        'store_id': [100, 200, 300, 100, 200, 300] * 10,
        'warehouse_code': [810.0, 130.0, 810.0, 130.0, 810.0, 130.0] * 10,
        'sales_amount': [10.5, 20.0, 15.5, 10.5, 20.0, 15.5] * 10
    }
    df = pd.DataFrame(data)
    
    print("DataFrame Original Dtypes:")
    print(df.dtypes)
    
    # Ejecutar Unified Clean (que llama a _classify_columns y aplica el Casting)
    cleaned_df, rules, notes, currency, schema = DataEngine.unify_and_clean({'Sheet1': df}, {})
    
    print("\nDataFrame Limpio Dtypes:")
    print(cleaned_df.dtypes)
    
    # Validaciones
    errors = []
    
    # 1. store_id debe ser OBJECT/STRING
    if cleaned_df['store_id'].dtype != 'object':
        errors.append(f"FALLO: 'store_id' sigue siendo {cleaned_df['store_id'].dtype}, debería ser object (string)")
    else:
        print("✅ 'store_id' convertido correctamente a String.")

    # 2. warehouse_code debe ser OBJECT/STRING
    if cleaned_df['warehouse_code'].dtype != 'object':
        errors.append(f"FALLO: 'warehouse_code' sigue siendo {cleaned_df['warehouse_code'].dtype}, debería ser object (string)")
    else:
        # Verificar contenido (debe ser "810.0" o "810")
        val = cleaned_df['warehouse_code'].iloc[0]
        print(f"✅ 'warehouse_code' convertido correctamente a String. Valor ejemplo: '{val}' (tipo: {type(val)})")

    # 3. sales_amount debe seguir siendo NUMERIC
    if not pd.api.types.is_numeric_dtype(cleaned_df['sales_amount']):
        errors.append(f"FALLO: 'sales_amount' se corrompió a {cleaned_df['sales_amount'].dtype}")
    else:
        print("✅ 'sales_amount' se mantuvo como Numérico.")

    if not errors:
        print("\n🎉 ÉXITO: El Escudo Anti-Métrica funciona (Strict ID Casting).")
    else:
        print("\n❌ FALLO EN VERIFICACIÓN DE CASTING:")
        for e in errors: print(f"  - {e}")

def verify_topology_exclusion():
    title("VERIFICACIÓN 2: Topology Exclusion (Semantic Translator)")
    
    # Simular una memoria que indica que ya agrupamos por 'Material'
    memory_context = """
    Resumen del análisis anterior:
    Tema: Ventas totales
    Filtros: Ninguno
    Agrupado por: [Material]
    Métrica: Total Ventas
    """
    
    prompt = "profundiza en esto"
    
    print(f"Memoria Simulada: {memory_context.strip()}")
    print(f"Prompt Usuario: '{prompt}'")
    
    # Llamar al Intent Classifier
    instruction = SemanticTranslator._classify_memory_intent(prompt, memory_context)
    
    print("\nInstrucción Generada por Ibis:")
    print("-" * 20)
    print(instruction)
    print("-" * 20)
    
    # Validaciones
    errors = []
    
    # 1. Debe detectar DRILL-DOWN
    if "MODO DRILL-DOWN" not in instruction:
        errors.append("FALLO: No detectó modo Drill-Down")
        
    # 2. Debe extraer la dimensión previa 'Material'
    if "Material" not in instruction:
        errors.append("FALLO: No extrajo la dimensión previa 'Material'")
        
    # 3. Debe generar la CONSTRAINT negativa
    if "CONSTRAINT: NO AGRUPES POR 'Material'" not in instruction:
        errors.append("FALLO: No generó la Constraint de Exclusión específica")
        
    if not errors:
        print("\n🎉 ÉXITO: La Exclusión Dinámica funciona (Topology Exclusion).")
    else:
        print("\n❌ FALLO EN VERIFICACIÓN DE TOPOLOGÍA:")
        for e in errors: print(f"  - {e}")

if __name__ == "__main__":
    try:
        verify_strict_id_casting()
        verify_topology_exclusion()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
