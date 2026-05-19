import sys
import pandas as pd
import numpy as np
from app.services.predictive_engine import PredictiveEngine

def test_forecasting():
    print("\n🔮 Testing Forecasting (Holt-Winters)...")
    
    # 1. Generate Synthetic Data (Linear Trend + Weekly Seasonality)
    dates = pd.date_range(start='2023-01-01', periods=24, freq='M')
    values = [100 + i*10 + (20 if i % 2 == 0 else -20) for i in range(24)]
    
    df = pd.DataFrame({'date': dates, 'sales': values})
    
    # 2. Run Forecast
    forecast = PredictiveEngine.forecast_series(df, 'date', 'sales', periods=3)
    
    # 3. Print Results
    print(f"Input Rows: {len(df)}")
    print(f"Output Rows: {len(forecast)}")
    
    if forecast:
        last_history = forecast[len(df)-1]
        first_forecast = forecast[len(df)]
        print(f"Last History: {last_history['date']} = {last_history['value']}")
        print(f"First Forecast: {first_forecast['date']} = {first_forecast['value']} (CI: {first_forecast['lower_ci']} - {first_forecast['upper_ci']})")
    else:
        print("❌ Forecast failed (Empty result)")

def test_anomalies():
    print("\n🚨 Testing Anomaly Detection (Isolation Forest)...")
    
    # 1. Generate Data with Outlier
    df = pd.DataFrame({'value': [10, 10, 10, 10, 10, 1000, 10, 10, 10]})
    
    # 2. Run Detection
    result = PredictiveEngine.detect_anomalies(df, 'value', contamination=0.1)
    
    # 3. Check Outlier
    outlier = result[result['is_anomaly']]
    if not outlier.empty:
        print(f"✅ Anomaly Detected at index {outlier.index[0]}: {outlier['value'].values[0]}")
    else:
        print("❌ Anomaly NOT detected")

if __name__ == "__main__":
    if PredictiveEngine.is_available():
        test_forecasting()
        test_anomalies()
    else:
        print("⚠️ Predictive Engine dependencies missing (statsmodels/sklearn). Skipping.")
