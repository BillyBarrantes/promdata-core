"""
🧬 TEST: Schema-Agnostic Engine V7 — Multi-Domain Verification
Tests that DataEngine._classify_columns() works with ANY file structure.
No hardcoded column names should appear in the classification logic.
"""
import pandas as pd
import numpy as np
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.'))

from app.services.data_engine import DataEngine

# ═══════════════════════════════════════════════════════════════════════════
# DOMAIN 1: INVENTORY (Latin American warehouse)
# ═══════════════════════════════════════════════════════════════════════════
def test_inventory():
    print("\n" + "="*80)
    print("🧪 TEST 1: INVENTORY FILE (Warehouse Stock)")
    print("="*80)
    
    df = pd.DataFrame({
        'codigo_material': ['MAT-001', 'MAT-002', 'MAT-003', 'MAT-004', 'MAT-005'],
        'descripcion_producto': ['Cemento Gris', 'Arena Fina', 'Bloque 15cm', 'Varilla 3/8', 'Clavo 2"'],
        'almacen': ['Lima', 'Lima', 'Arequipa', 'Arequipa', 'Cusco'],
        'stock_disponible': [1500, 3200, 800, 2100, 450],
        'precio_unitario': [28.50, 15.00, 3.80, 42.00, 8.50],
        'fecha_ingreso': pd.date_range('2024-01-15', periods=5, freq='M')
    })
    
    schema = DataEngine._classify_columns(df)
    
    # Assertions
    assert schema['stock_disponible']['role'] == 'metric', f"Stock should be metric, got {schema['stock_disponible']['role']}"
    assert schema['precio_unitario']['role'] == 'metric', f"Price should be metric, got {schema['precio_unitario']['role']}"
    assert schema['almacen']['role'] == 'dimension', f"Warehouse should be dimension, got {schema['almacen']['role']}"
    assert schema['fecha_ingreso']['role'] == 'date', f"Date should be date, got {schema['fecha_ingreso']['role']}"
    assert schema['descripcion_producto']['role'] == 'dimension', f"Description should be dimension, got {schema['descripcion_producto']['role']}"
    
    print("✅ INVENTORY: All assertions passed!")
    return True

# ═══════════════════════════════════════════════════════════════════════════
# DOMAIN 2: SALES (E-commerce transactions)
# ═══════════════════════════════════════════════════════════════════════════
def test_sales():
    print("\n" + "="*80)
    print("🧪 TEST 2: SALES FILE (E-commerce)")
    print("="*80)
    
    np.random.seed(42)
    n = 100
    df = pd.DataFrame({
        'order_id': [f'ORD-{i:05d}' for i in range(n)],
        'customer_name': np.random.choice(['Alice', 'Bob', 'Carlos', 'Diana', 'Eve'], n),
        'product_category': np.random.choice(['Electronics', 'Clothing', 'Food', 'Books'], n),
        'revenue': np.random.uniform(10, 500, n).round(2),
        'quantity_sold': np.random.randint(1, 20, n),
        'transaction_date': pd.date_range('2024-01-01', periods=n, freq='D')
    })
    
    schema = DataEngine._classify_columns(df)
    
    assert schema['revenue']['role'] == 'metric', f"Revenue should be metric, got {schema['revenue']['role']}"
    assert schema['quantity_sold']['role'] == 'metric', f"Quantity should be metric, got {schema['quantity_sold']['role']}"
    assert schema['product_category']['role'] == 'dimension', f"Category should be dimension, got {schema['product_category']['role']}"
    assert schema['transaction_date']['role'] == 'date', f"Date should be date, got {schema['transaction_date']['role']}"
    assert schema['order_id']['role'] == 'identifier', f"OrderID should be identifier, got {schema['order_id']['role']}"
    
    print("✅ SALES: All assertions passed!")
    return True

# ═══════════════════════════════════════════════════════════════════════════
# DOMAIN 3: HR / MEDICAL (Completely novel columns)
# ═══════════════════════════════════════════════════════════════════════════
def test_hr_medical():
    print("\n" + "="*80)
    print("🧪 TEST 3: HR/MEDICAL FILE (Novel Columns)")
    print("="*80)
    
    np.random.seed(42)
    n = 50
    df = pd.DataFrame({
        'employee_number': [f'EMP{i:04d}' for i in range(n)],
        'department': np.random.choice(['Engineering', 'Sales', 'HR', 'Marketing'], n),
        'hire_date': pd.date_range('2020-01-01', periods=n, freq='15D'),
        'salary_annual': np.random.uniform(40000, 120000, n).round(2),
        'performance_score': np.random.uniform(1.0, 5.0, n).round(1),
        'days_absent': np.random.randint(0, 30, n),
        'office_location': np.random.choice(['NYC', 'SF', 'Austin', 'Remote'], n)
    })
    
    schema = DataEngine._classify_columns(df)
    
    assert schema['salary_annual']['role'] == 'metric', f"Salary should be metric, got {schema['salary_annual']['role']}"
    assert schema['performance_score']['role'] == 'metric', f"Score should be metric, got {schema['performance_score']['role']}"
    assert schema['department']['role'] == 'dimension', f"Department should be dimension, got {schema['department']['role']}"
    assert schema['hire_date']['role'] == 'date', f"Hire date should be date, got {schema['hire_date']['role']}"
    assert schema['office_location']['role'] == 'dimension', f"Location should be dimension, got {schema['office_location']['role']}"
    
    print("✅ HR/MEDICAL: All assertions passed!")
    return True

# ═══════════════════════════════════════════════════════════════════════════
# DOMAIN 4: TOPOLOGY DETECTION (Data-driven, no keywords)
# ═══════════════════════════════════════════════════════════════════════════
def test_topology():
    print("\n" + "="*80)
    print("🧪 TEST 4: TOPOLOGY DETECTION (Data-Driven)")
    print("="*80)
    
    df = pd.DataFrame({
        'period': pd.date_range('2024-01-01', periods=12, freq='M'),
        'metric_a': np.random.uniform(100, 200, 12).round(2),  # Flow: variable
        'metric_b': [50.0] * 12,  # Snapshot: constant (CV < 0.1)
    })
    
    schema = DataEngine._classify_columns(df)
    topology = DataEngine._detect_topology(df, schema)
    
    print(f"  Topology: {topology}")
    
    assert 'FLOW' in topology.get('metric_a', ''), f"metric_a should be FLOW, got {topology.get('metric_a', '')}"
    assert 'SNAPSHOT' in topology.get('metric_b', ''), f"metric_b should be SNAPSHOT, got {topology.get('metric_b', '')}"
    
    print("✅ TOPOLOGY: All assertions passed!")
    return True

# ═══════════════════════════════════════════════════════════════════════════
# DOMAIN 5: GRAMMAR VALIDATION (New Intents)
# ═══════════════════════════════════════════════════════════════════════════
def test_grammar():
    print("\n" + "="*80)
    print("🧪 TEST 5: SEMANTIC GRAMMAR (New Intent Types)")
    print("="*80)
    
    from app.core.semantic_grammar import (
        AnalysisPlan, DescriptiveIntent, TimeTrendIntent, DistributionIntent,
        DiagnosticIntent, PredictiveIntent, VisualProtocol
    )
    
    # Test DiagnosticIntent
    diag = DiagnosticIntent(
        rationale="Analyze variability of salary by department",
        metric="salary_annual",
        dimension="department",
        visual_protocol=VisualProtocol.BOXPLOT
    )
    assert diag.type == "diagnostic"
    
    # Test PredictiveIntent
    pred = PredictiveIntent(
        rationale="Forecast next 6 months of revenue",
        date_column="transaction_date",
        value_column="revenue",
        analysis_subtype="forecast",
        horizon=6
    )
    assert pred.type == "predictive"
    
    # Test AnalysisPlan with new intents
    plan_diag = AnalysisPlan(
        main_intent=diag,
        title="Variabilidad Salarial por Departamento"
    )
    assert plan_diag.main_intent.type == "diagnostic"
    
    plan_pred = AnalysisPlan(
        main_intent=pred,
        title="Proyección de Ingresos"
    )
    assert plan_pred.main_intent.type == "predictive"
    
    print("✅ GRAMMAR: All assertions passed!")
    return True


if __name__ == '__main__':
    results = []
    
    print("\n" + "🧬"*40)
    print("🧬 SCHEMA-AGNOSTIC ENGINE V7 — VERIFICATION SUITE")
    print("🧬"*40)
    
    tests = [test_inventory, test_sales, test_hr_medical, test_topology, test_grammar]
    
    for test_fn in tests:
        try:
            result = test_fn()
            results.append(('✅', test_fn.__name__, 'PASSED'))
        except Exception as e:
            results.append(('❌', test_fn.__name__, str(e)))
            print(f"❌ {test_fn.__name__} FAILED: {e}")
    
    print("\n" + "="*80)
    print("📊 RESULTS SUMMARY:")
    print("="*80)
    for emoji, name, status in results:
        print(f"  {emoji} {name}: {status}")
    
    passed = sum(1 for r in results if r[0] == '✅')
    total = len(results)
    print(f"\n  📈 {passed}/{total} tests passed")
    
    if passed == total:
        print("  🎉 ALL TESTS PASSED — Schema-Agnostic Engine V7 is operational!")
    else:
        print("  ⚠️ Some tests failed — review needed.")
        sys.exit(1)
