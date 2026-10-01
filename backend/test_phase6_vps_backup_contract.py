from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = ROOT_DIR / "scripts" / "vps_backup.sh"


def test_backup_waits_past_stale_idle_status_until_requested_snapshot_finishes(tmp_path: Path) -> None:
    """A prior idle/ok state must not authorize copying the old dump.rdb."""
    (tmp_path / ".env").write_text("APP_DOMAIN=app.example.com\n", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text("DEEPSEEK_API_KEY=test\n", encoding="utf-8")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

args = sys.argv[1:]
state = Path(os.environ["FAKE_DOCKER_STATE"])
events = Path(os.environ["FAKE_DOCKER_EVENTS"])
events.write_text(events.read_text() + " ".join(args) + "\\n" if events.exists() else " ".join(args) + "\\n")

if args[:2] == ["compose", "version"]:
    print("Docker Compose version v2.test")
    raise SystemExit(0)

if "INFO" in args and "persistence" in args:
    count = int(state.read_text()) + 1 if state.exists() else 1
    state.write_text(str(count))
    progress, last_save = {
        1: ("0", "100"),  # Baseline before BGSAVE.
        2: ("0", "100"),  # Stale idle/ok response must not copy.
        3: ("1", "100"),  # Observe the new background save.
        4: ("0", "101"),  # New save completed successfully.
    }.get(count, ("0", "101"))
    print("# Persistence")
    print(f"rdb_bgsave_in_progress:{progress}")
    print("rdb_last_bgsave_status:ok")
    print(f"rdb_last_save_time:{last_save}")
    raise SystemExit(0)

if "BGSAVE" in args:
    print("Background saving started")
    raise SystemExit(0)

if "redis-check-rdb" in args:
    print("RDB looks OK")
    raise SystemExit(0)

if "cp" in args:
    target = Path(args[-1])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"validated-rdb")
    raise SystemExit(0)

raise SystemExit(f"unexpected docker arguments: {args}")
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    event_log = tmp_path / "docker-events.log"
    state_file = tmp_path / "info-count"

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_DOCKER_STATE": str(state_file),
            "FAKE_DOCKER_EVENTS": str(event_log),
        }
    )
    completed = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Snapshot verificado:" in completed.stdout
    events = event_log.read_text(encoding="utf-8").splitlines()
    persistence_reads = [i for i, event in enumerate(events) if "INFO persistence" in event]
    check_index = next(i for i, event in enumerate(events) if "redis-check-rdb" in event)
    copy_index = next(i for i, event in enumerate(events) if " cp " in f" {event} ")

    assert len(persistence_reads) == 4
    assert persistence_reads[1] < persistence_reads[2] < persistence_reads[3] < check_index < copy_index
    backup = next((tmp_path / "backups" / "redis").glob("*.rdb"))
    assert backup.read_bytes() == b"validated-rdb"
    assert backup.stat().st_mode & 0o777 == 0o600
