import sys
import os
import json

# Add backend directory to sys.path to allow imports
sys.path.append(os.path.join(os.getcwd(), 'backend'))

try:
    from app.services.chart_factory import ChartFactory
    print("✅ ChartFactory imported successfully")
except ImportError as e:
    print(f"❌ Failed to import ChartFactory: {e}")
    sys.exit(1)

def test_waterfall():
    print("\n--- Testing Waterfall Chart ---")
    data = [
        {'name': 'Ingreso Bruto', 'value': 1000},
        {'name': 'Costo de Ventas', 'value': -400},
        {'name': 'Gastos Operativos', 'value': -200},
        {'name': 'Impuestos', 'value': -100},
        {'name': 'Otros Ingresos', 'value': 50}
    ]
    try:
        chart = ChartFactory.create_chart('waterfall', 'P&L Analysis', data)
        if isinstance(chart, dict) and 'series' in chart:
            print("✅ [WATERFALL] Generado correctamente")
            # Optional: Check if totals match expectations
            series_data = chart['series'][1]['data'] # Value series
            final_val = series_data[-1]['value'] # Total column value
            print(f"   Input Net: {1000-400-200-100+50} | Chart Total: {final_val}")
        else:
            print("❌ [WATERFALL] Invalid output format")
    except Exception as e:
        print(f"❌ [WATERFALL] Error: {e}")

def test_heatmap():
    print("\n--- Testing Heatmap ---")
    # Pasillo, Hora, Trafico
    data = [
        {'pasillo': 'A', 'hora': '08:00', 'trafico': 10},
        {'pasillo': 'A', 'hora': '09:00', 'trafico': 40},
        {'pasillo': 'B', 'hora': '08:00', 'trafico': 5},
        {'pasillo': 'B', 'hora': '09:00', 'trafico': 15},
        {'pasillo': 'C', 'hora': '08:00', 'trafico': 20},
        {'pasillo': 'C', 'hora': '10:00', 'trafico': 50}
    ]
    try:
        # Assuming dictionary inputs are handled, or list of lists if strictly following our internal logic?
        # The new implementation takes list of dicts and converts to DF.
        chart = ChartFactory.create_chart('heatmap', 'Traffic Heatmap', data, x_label='hora', y_label='pasillo')
        if isinstance(chart, dict) and 'visualMap' in chart:
            print("✅ [HEATMAP] Generado correctamente")
        else:
            print("❌ [HEATMAP] Invalid output format")
    except Exception as e:
        print(f"❌ [HEATMAP] Error: {e}")

def test_smart_pie():
    print("\n--- Testing Smart Pie Chart (Top 5 + Others) ---")
    data = [
        {'name': 'Prod A', 'value': 100},
        {'name': 'Prod B', 'value': 90},
        {'name': 'Prod C', 'value': 80},
        {'name': 'Prod D', 'value': 70},
        {'name': 'Prod E', 'value': 60}, # Top 5 ends here
        {'name': 'Prod F', 'value': 10},
        {'name': 'Prod G', 'value': 5},
        {'name': 'Prod H', 'value': 2},
        {'name': 'Prod I', 'value': 1},
        {'name': 'Prod J', 'value': 1}
    ]
    try:
        chart = ChartFactory.create_chart('pie', 'Sales Distribution', data)
        if isinstance(chart, dict) and 'series' in chart:
            series_data = chart['series'][0]['data']
            print(f"   Input Items: {len(data)} | Chart Slices: {len(series_data)}")
            
            has_others = any(d['name'] == 'OTROS' for d in series_data)
            if len(series_data) == 6 and has_others:
                 print("✅ [PIE SMART] Agrupación 'OTROS' exitosa")
            else:
                 print(f"❌ [PIE SMART] Fallo en agrupación. Slices: {len(series_data)}")
        else:
            print("❌ [PIE SMART] Invalid output format")
    except Exception as e:
        print(f"❌ [PIE SMART] Error: {e}")

if __name__ == "__main__":
    test_waterfall()
    test_heatmap()
    test_smart_pie()
