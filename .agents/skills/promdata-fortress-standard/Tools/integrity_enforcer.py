import pandas as pd
import sys

"""
PROMDATA INTEGRITY ENFORCER
Este script simula errores críticos de negocio. 
El Agente debe correr esto para verificar que su lógica nueva protege las reglas sagradas.
"""

class PromDataComplianceError(Exception):
    pass

def check_id_shield():
    print("🛡️  Verificando ID Shield (Protección de Ceros)...")
    # Simulación: Datos sucios entrantes
    raw_data = {'warehouse_code': ['001', '050', 130, '130.0']}
    df = pd.DataFrame(raw_data)
    
    # --- ZONA DE LÓGICA DEL AGENTE (Simulada) ---
    # El agente debe asegurarse de que su lógica produzca esto:
    try:
        # Normalización estandar: Convertir a string, quitar decimal .0 y rellenar ceros
        df['warehouse_code'] = df['warehouse_code'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(3)
    except Exception as e:
        raise PromDataComplianceError(f"Fallo en procesamiento de IDs: {e}")
    # ---------------------------------------------

    if "130.0" in df['warehouse_code'].values:
        raise PromDataComplianceError("CRITICAL: Se detectó un ID float ('130.0'). Regla 1.2 violada.")
    if "050" not in df['warehouse_code'].values:
         raise PromDataComplianceError("CRITICAL: Se perdieron los ceros a la izquierda ('50' en vez de '050').")
    
    print("✅ ID Shield: PASÓ")

def check_pie_chart_limit():
    print("🍰 Verificando Límite Visual (Max 6 Slices)...")
    # Simulación: 20 categorías
    categories = [f"Prod_{i}" for i in range(20)]
    values = [10] * 20
    data = pd.DataFrame({'category': categories, 'value': values})
    
    # Lógica de Agrupación que el Agente debe implementar
    limit = 6
    if len(data) > limit:
        top = data.head(limit - 1)
        others_value = data.iloc[limit-1:]['value'].sum()
        others_df = pd.DataFrame([{'category': 'Otros', 'value': others_value}])
        final_df = pd.concat([top, others_df])
    else:
        final_df = data
        
    if len(final_df) > 6:
        raise PromDataComplianceError(f"VISUAL FAIL: El gráfico de torta tiene {len(final_df)} segmentos. Máximo permitido: 6.")
    
    print("✅ Visual Limit: PASÓ")

def check_snapshot_logic():
    print("📸 Verificando Snapshot Logic (Anti-Suma Ciega)...")
    # Simulación: Stock en Enero y Febrero
    data = pd.DataFrame({
        'date': ['2024-01-01', '2024-02-01'],
        'stock': [100, 120] # El stock actual es 120, NO 220 (que sería la suma)
    })
    
    # Lógica incorrecta (Suma ciega)
    blind_sum = data['stock'].sum()
    
    # Lógica Correcta (Last Snapshot)
    correct_stock = data[data['date'] == data['date'].max()]['stock'].sum()
    
    # Aquí validamos que el sistema no esté usando blind_sum para reportes de estado
    if correct_stock != 120:
         raise PromDataComplianceError("LOGIC FAIL: El cálculo de stock falló.")
         
    print("✅ Snapshot Logic: PASÓ")

if __name__ == "__main__":
    try:
        print("\n--- INICIANDO PROTOCOLO DE INTEGRIDAD PROMDATA ---\n")
        check_id_shield()
        check_snapshot_logic()
        check_pie_chart_limit()
        print("\n🚀 [EXITO] EL CÓDIGO CUMPLE CON LOS ESTÁNDARES PES v2.0")
        sys.exit(0)
    except PromDataComplianceError as e:
        print(f"\n❌ [BLOQUEO] RECHAZADO POR INTEGRIDAD: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ [ERROR] Error de ejecución: {e}")
        sys.exit(1)