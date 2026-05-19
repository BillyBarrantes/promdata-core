
import pandas as pd
import ibis
import os
from app.services.ibis_engine import IbisEngine
from app.services.data_engine import DataEngine

# 1. Create a Mock Parquet File with "Sabotage-Prone" Data
# Column 'store_id' is technically strings like "810", "811", but semantically IDs.
# Column 'amount' is metric.
print("\n🧪 [TEST] Creating Mock Data...")
df = pd.DataFrame({
    'store_id': ['810', '811', '812', '810', '811'],
    'category': ['Food', 'Food', 'Non-Food', 'Food', 'Non-Food'],
    'amount': [100.0, 200.0, 150.0, 50.0, 30.0]
})

parquet_path = "mock_data_4d.parquet"
df.to_parquet(parquet_path)
print(f"✅ Mock Parquet created at {parquet_path}")

# 2. Simulate Schema Profile extraction (as done in DataEngine)
# 'store_id' is identified as a Dimension -> Protected!
schema_profile = {
    'store_id': {'role': 'dimension', 'type': 'categorical'},
    'category': {'role': 'dimension', 'type': 'categorical'},
    'amount': {'role': 'metric', 'type': 'numeric'}
}

protected_cols = [c for c, info in schema_profile.items() 
                  if info['role'] in ['dimension', 'identifier']]

print(f"🛡️ [TEST] Protected Columns identified: {protected_cols}")
assert 'store_id' in protected_cols, "store_id should be protected!"

# 3. Test IbisEngine.execute_plan with Immutability Lock
# We create a plan that groups by 'store_id'.
class MockIntent:
    type = 'descriptive'
    metrics = ['amount']
    group_by = ['store_id']
    aggregation = 'sum'
    visual_protocol = None
    filters = []

class MockPlan:
    main_intent = MockIntent()
    title = "Analysis by Store ID"
    glossary_hint = None
    metric_polarity = 'neutral'

print("\n🧪 [TEST] Executing Plan with Immutability Lock...")
try:
    result = IbisEngine.execute_plan(parquet_path, MockPlan(), protected_cols=protected_cols)
    
    # 4. Verify Context Injection
    # We expect labels to be "Store Id 810", not just "810"
    print("\n📊 [RESULTS] Checking Context Injection...")
    chart_data = result['data']
    
    context_injected = False
    for item in chart_data:
        name = item['name']
        print(f"   - Label: '{name}'")
        if "Store Id 810" in name or "Store Id 811" in name or "Store Id 812" in name:
            context_injected = True
            
    if context_injected:
         print("\n✅ PASS: Context Injection worked! Labels contain dimension name.")
    else:
         print("\n❌ FAIL: Context Injection failed. Labels are naked IDs.")
         
    # 5. Verify Immutability (Type Check)
    # We expect 'store_id' to REMAIN string in Ibis logic, avoiding float cast issues.
    # While we can't easily introspect the internal Ibis table type here without mocking _auto_cast_columns prints,
    # the fact that execute_plan ran without error on 'store_id' grouping suggests it handled it correctly.
    # To be sure, let's check if the labels are NOT "810.0" (which would happen if it converted to float then back to string)
    
    is_float_artifact = any(".0" in item['name'] for item in chart_data if "Store Id" not in item['name']) # If context injection worked, .0 might be there but prepended. 
    # Actually, if auto-cast to float happened, "810" -> 810.0. 
    # Context injection does str(val). If val is 810.0, str(val) is "810.0".
    # Logic: is_numeric = val_str.replace('.', '', 1).isdigit() -> True for "810.0"
    # Result: "Store Id 810.0" provided context injection works on floats too.
    
    # But strictly, we want it to stay STRING "810".
    # If it stayed string, str("810") is "810".
    # Result: "Store Id 810".
    
    has_decimal_artifacts = any(".0" in item['name'] for item in chart_data)
    if not has_decimal_artifacts:
        print("✅ PASS: Immutability Lock worked! No '.0' artifacts found in labels.")
    else:
        print("❌ FAIL: Immutability Lock failed. Found '.0' behavior (Float casting occurred).")

except Exception as e:
    print(f"❌ FAIL: Execution Error: {e}")
    import traceback
    traceback.print_exc()

# Cleanup
if os.path.exists(parquet_path):
    os.remove(parquet_path)
