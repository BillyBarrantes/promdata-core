import pandas as pd
import numpy as np
from app.services.predictive_engine import PredictiveEngine

def test_transactional_stock_aggregation():
    print("\n📦 Testing Transactional Stock Data (Multiple Updates per SKU per Day)...")
    
    # Scenario: 
    # SKU 'A' has 3 updates on Jan 31st:
    # 10:00 AM -> 10 units
    # 02:00 PM -> 15 units
    # 06:00 PM -> 50 units (This is the True Closing Stock)
    #
    # If we SUM -> 10+15+50 = 75 (WRONG)
    # If we LAST -> 50 (CORRECT)
    
    dates = pd.to_datetime(['2024-01-31'] * 3)
    df = pd.DataFrame({
        'date': dates,
        'sku': ['A', 'A', 'A'],
        'stock': [10, 15, 50]
    })
    
    # We add another day to ensure forecasting works (needs >4 points usually, but logic test is pre-forecast)
    # Actually, forecast_series returns EMPTY if <4 points.
    # We will test the internal logic by calling grouping manually or blindly trusting the forecast won't crash 
    # but the VALUE is what matters. To get a value we need more data.
    
    # Let's create a full month of data where every day has 3 updates for SKU A.
    # Daily True Stock = 50.
    # Daily Sum Bad Stock = 75.
    
    date_rng = pd.date_range('2024-01-01', '2024-04-30', freq='D')
    rows = []
    for d in date_rng:
        # Update 1
        rows.append({'date': d, 'sku': 'A', 'stock': 10})
        # Update 2
        rows.append({'date': d, 'sku': 'A', 'stock': 15})
        # Final Update (True)
        rows.append({'date': d, 'sku': 'A', 'stock': 50})
        
    df_full = pd.DataFrame(rows)
    
    print("\n👉 Test 4: Transactional Stock (3 updates/day) - Expecting 50 (Last), NOT 75 (Sum)")
    # We expect the Monthly Forecast to be based on the DAILY LAST (50).
    fcst = PredictiveEngine.forecast_series(df_full, 'date', 'stock', periods=1, aggregation_method='last')
    
    if fcst:
        val = fcst[0]['value']
        print(f"   Result (Jan): {val:,.2f}")
        
        if 49 <= val <= 51:
             print("   ✅ SUCCESS: Took the LAST update of the day (50)")
        elif val >= 70:
             print("   ❌ FAILURE: Summed all updates (75)")
        else:
             print(f"   ❌ FAILURE: Unexpected value {val}")
    else:
        print("   ⚠️ No forecast returned (maybe not enough data?)")

if __name__ == "__main__":
    if PredictiveEngine.is_available():
        test_transactional_stock_aggregation()
    else:
        print("⚠️ Dependencies missing.")
