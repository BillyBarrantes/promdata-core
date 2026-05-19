import sys
import os
import pandas as pd
from unittest.mock import MagicMock

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.services.data_engine import DataEngine

def test_unify_and_clean_fix():
    print("--- Testing DataEngine Fix ---")
    
    # 1. Create Dummy DFs
    df = pd.DataFrame({
        "Producto": ["A", "B"],
        "Ventas (S/)": [100, 200],
        "Fecha": ["2023-01-01", "2023-01-01"]
    })
    dfs = {"principal": df}
    glossary = {}
    
    try:
        # 2. Call unify_and_clean
        # It should return 4 values now
        result = DataEngine.unify_and_clean(dfs, glossary)
        
        if len(result) == 4:
            print("✅ unify_and_clean returned 4 values.")
            main_df, rules, notes, currency = result
            print(f"   - Rules Type: {type(rules)}")
            print(f"   - Currency: {currency}")
            
            # Check topology inside
            print(f"   - Detected Rules: {rules}")
        else:
            print(f"❌ unify_and_clean returned {len(result)} values. Expected 4.")
            
    except Exception as e:
        print(f"❌ Error calling unify_and_clean: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_unify_and_clean_fix()
