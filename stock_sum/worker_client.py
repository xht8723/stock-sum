"""Parent-side helpers for launching short-lived stock-sum workers."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import subprocess
import sys
import tempfile

from stock_sum.config.models import AppConfig


def run_cli_worker(config: AppConfig, operation: str, payload: dict[str, Any]) -> int:
    """Run a heavy CLI operation in a child process and mirror its output."""

    with tempfile.TemporaryDirectory(prefix="stock-sum-worker-") as temp_dir:
        request_path = Path(temp_dir) / "worker-request.json"
        request_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": operation,
                    "job_id": None,
                    "config": config.model_dump(mode="json"),
                    "payload": payload,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, "-m", "stock_sum.worker", "--request", str(request_path)],
            check=False,
            capture_output=True,
            text=True,
        )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode
