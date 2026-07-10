import ibis
import pandas as pd
from pprint import pprint

# Create a mock table with Ibis
con = ibis.duckdb.connect()
con.execute("""
    CREATE TABLE employees (
        id_empleado VARCHAR,
        cargo VARCHAR,
        fecha_contratacion DATE,
        estado VARCHAR
    );
    INSERT INTO employees VALUES
        ('1', 'Analista', '2021-01-01', 'Activo'),
        ('2', 'Analista', '2021-02-01', 'Inactivo'),
        ('3', 'Coordinador', '2021-01-01', 'Activo');
""")
t = con.table('employees')

# Mimic the filter
t_filtered = t.filter(t['estado'].upper() == 'ACTIVO')

# Mimic trunc_op
col_date = t_filtered['fecha_contratacion']
trunc_op = col_date.truncate('M')

# Mimic multi-series aggregation
split_dim = 'cargo'
col_val = t_filtered['id_empleado']
agg_expr = col_val.count()

# 1. Top cats
top_cats = (
    t_filtered.group_by(split_dim)
    .aggregate(vol_total=agg_expr)
    .order_by(ibis.desc('vol_total'))
    .limit(5)
)
top_items = top_cats[split_dim].to_pandas().tolist()
print("Top items:", top_items)

# 2. Filter and mutate
t_split = t_filtered.filter(t_filtered[split_dim].isin(top_items))
t_split = t_split.mutate(periodo=trunc_op)

# 3. Aggregate
agged_multi = (
    t_split.group_by(['periodo', split_dim])
    .aggregate(valor=agg_expr)
    .order_by('periodo')
)

print("\n--- SQL Generated ---")
print(ibis.to_sql(agged_multi))

print("\n--- Data ---")
print(agged_multi.to_pandas())
