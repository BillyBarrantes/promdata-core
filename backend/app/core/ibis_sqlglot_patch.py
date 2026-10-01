"""Defensive runtime compatibility patch: sqlglot <-> ibis-framework on DuckDB.

In sqlglot >= 30.12.0, `sqlglot.expressions.Drop` deprecated `this` in favor of `tables`.
However, `ibis-framework` (<=12.x) constructs drop expressions like:
    sge.Drop(kind="VIEW", this=table.get_name(), exists=True)
    sge.Drop(kind="TABLE", this=final_table, exists=True)

In sqlglot >= 30.12.0, `this` is ignored if `tables` is not set, resulting in:
    "DROP VIEW IF EXISTS " (without view name)
or
    "DROP TABLE IF EXISTS " (without table name)
which causes DuckDB to fail with:
    ParserException: Parser Error: syntax error at end of input

This module applies an idempotent monkeypatch to `Generator.drop_sql` ensuring that
any `this` argument on a Drop expression is automatically mapped to `tables` if
`tables` is empty or unset.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def apply_ibis_sqlglot_patch() -> bool:
    """Applies the sqlglot drop_sql compatibility patch if needed.

    Returns True if the patch was applied, False if already applied or unavailable.
    """
    try:
        from sqlglot.generator import Generator
        from sqlglot import expressions as exp
    except ImportError:
        return False

    if getattr(Generator, "_promdata_ibis_drop_patched", False):
        return False

    orig_drop_sql = Generator.drop_sql

    def _safe_drop_sql(self: Generator, expression: exp.Drop) -> str:
        if not expression.args.get("tables") and "this" in expression.args:
            this_val = expression.args["this"]
            if isinstance(this_val, str):
                expression.set("tables", [exp.to_identifier(this_val)])
            elif isinstance(this_val, list):
                expression.set(
                    "tables",
                    [exp.to_identifier(x) if isinstance(x, str) else x for x in this_val],
                )
            elif this_val is not None:
                expression.set("tables", [this_val])
        return orig_drop_sql(self, expression)

    Generator.drop_sql = _safe_drop_sql  # type: ignore[assignment]
    Generator._promdata_ibis_drop_patched = True
    return True
