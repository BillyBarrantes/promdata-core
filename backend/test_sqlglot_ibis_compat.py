"""Regression tests for sqlglot <-> ibis-framework compatibility with DuckDB.

Guarantees that:
1. Generator.drop_sql correctly handles `this` arguments passed by ibis.
2. DuckDB create_table with memtables never produces truncated SQL.
3. Complex table names containing colons (e.g., 'primary__sheet::Sheet1') work cleanly.
"""

from __future__ import annotations

import ibis
import pandas as pd
import pytest
import sqlglot as sg
from sqlglot import expressions as exp

from app.core.ibis_sqlglot_patch import apply_ibis_sqlglot_patch


def test_sqlglot_drop_patch_generates_valid_sql():
    """Verify that drop expressions with 'this' argument render identifiers correctly."""
    apply_ibis_sqlglot_patch()

    d_view = exp.Drop(kind="VIEW", this="sample_view_name", exists=True).sql("duckdb")
    assert "sample_view_name" in d_view
    assert d_view == "DROP VIEW IF EXISTS sample_view_name"

    d_table = exp.Drop(kind="TABLE", this=sg.table("sample_table_name"), exists=True).sql("duckdb")
    assert "sample_table_name" in d_table
    assert d_table == "DROP TABLE IF EXISTS sample_table_name"

    d_schema = exp.Drop(kind="SCHEMA", this="sample_schema_name").sql("duckdb")
    assert "sample_schema_name" in d_schema
    assert d_schema == "DROP SCHEMA sample_schema_name"


def test_ibis_duckdb_create_table_with_memtable():
    """Verify that ibis.duckdb create_table successfully creates, populates,

    and cleans up temporary memtables without ParserException.
    """
    apply_ibis_sqlglot_patch()

    con = ibis.duckdb.connect()
    df = pd.DataFrame({
        "centro_costo": ["CC-IT", "CC-Ventas", "CC-Finanzas"],
        "monto": [1500.50, 3200.00, 4800.75],
        "año": [2025, 2025, 2025],
    })

    # Test standard table creation with overwrite
    tbl = con.create_table("test_memtable_compat", df, overwrite=True)
    assert tbl.count().execute() == 3

    # Test table with :: in name as used by canonical materialized frames
    frame_table_name = "primary__sheet::Sheet1"
    tbl_frame = con.create_table(frame_table_name, df, overwrite=True)
    assert tbl_frame.count().execute() == 3

    # Verify query execution on the created table
    filtered = tbl_frame.filter(tbl_frame["centro_costo"] == "CC-IT")
    res_df = filtered.to_pandas()
    assert len(res_df) == 1
    assert res_df.iloc[0]["centro_costo"] == "CC-IT"
    assert res_df.iloc[0]["monto"] == 1500.50
