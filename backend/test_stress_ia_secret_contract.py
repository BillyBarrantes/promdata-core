"""Security contract for the manual stress-test credential source."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("test_stress_ia.py")
JWT_LITERAL_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)


def test_stress_script_requires_environment_token_without_embedding_jwt() -> None:
    """The manual stress script must fail closed without a token."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert JWT_LITERAL_PATTERN.search(source) is None
    assert 'os.getenv("SUPABASE_TEST_TOKEN", "")' in source

    environment = os.environ.copy()
    environment.pop("SUPABASE_TEST_TOKEN", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=SCRIPT_PATH.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0
    assert "SUPABASE_TEST_TOKEN" in result.stdout
