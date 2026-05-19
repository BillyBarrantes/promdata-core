import sys
import os
import pandas as pd
import json
from unittest.mock import MagicMock, patch

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.services.auto_analyst import AutoAnalyst

def test_waterfall_flow():
    print("--- Testing AutoAnalyst Waterfall Flow ---")
    
    # 1. Create Dummy Financial Data
    df = pd.DataFrame({
        "Concepto": ["Ingresos", "Costos", "Gastos Op", "Impuestos", "Otros Ingresos"],
        "Monto": [1000, -600, -200, -50, 20],
        "Fecha": ["2023-01-01"] * 5
    })
    
    # 2. Mock Gemini Response
    mock_response_json = {
        "detected_domain": "Finance",
        "strategic_intent": "Financial Flow",
        "recommended_chart": "waterfall", # <--- KEY TRIGGER
        "quantity_term": "USD",
        "risk_concept": "Perdida",
        "entity_name": "Account"
    }
    
    # 3. Patch GenerativeModel to avoid API calls and force intent
    with patch('google.generativeai.GenerativeModel') as MockModel:
        mock_instance = MockModel.return_value
        mock_instance.generate_content.return_value.text = json.dumps(mock_response_json)
        
        # 4. Run Analysis
        currency_meta = {"symbol": "S/", "code": "PEN"}
        facts = AutoAnalyst.analyze(df, currency_meta=currency_meta)
        
        # 5. Verify Results
        print(f"Context Intent: {facts['contexto'].get('strategic_intent')}")
        print(f"Recommended Chart: {facts['contexto'].get('recommended_chart')}")
        
        advanced = facts.get('analisis_avanzado', [])
        if advanced and advanced[0]['tipo'] == 'waterfall':
            print("✅ Waterfall Chart Generated!")
            print(f"Title: {advanced[0].get('title')}")
            print(f"Data Points: {len(advanced[0].get('chart_data'))}")
        else:
            print("❌ Waterfall Chart NOT Generated")
            print(f"Actual Advanced: {advanced}")

def test_heatmap_flow():
    print("\n--- Testing AutoAnalyst Heatmap Flow ---")
    
    # Heatmap needs 2 cats + 1 num
    df = pd.DataFrame({
        "Dia": ["Lunes", "Lunes", "Martes", "Martes"],
        "Hora": ["10am", "11am", "10am", "11am"],
        "Ventas": [10, 20, 15, 25]
    })
    
    mock_response_json = {
        "detected_domain": "Retail",
        "strategic_intent": "Density",
        "recommended_chart": "heatmap", # <--- KEY TRIGGER
        "quantity_term": "Units",
        "risk_concept": "Low Traffic",
        "entity_name": "Hour"
    }
    
    with patch('google.generativeai.GenerativeModel') as MockModel:
        mock_instance = MockModel.return_value
        mock_instance.generate_content.return_value.text = json.dumps(mock_response_json)
        
        facts = AutoAnalyst.analyze(df)
        
        advanced = facts.get('analisis_avanzado', [])
        if advanced and advanced[0]['tipo'] == 'heatmap':
            print("✅ Heatmap Generated!")
        else:
             print("❌ Heatmap NOT Generated")

if __name__ == "__main__":
    test_waterfall_flow()
    test_heatmap_flow()
