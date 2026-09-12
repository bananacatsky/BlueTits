"""Run the frontend and backend development servers together."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def main() -> int:
    commands = (
        (sys.executable, "backend/server.py"),
        ("npm", "--prefix", "frontend", "run", "dev"),
    )
    processes: list[subprocess.Popen] = []

    try:
        for command in commands:
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=PROJECT_DIR,
                    start_new_session=True,
                )
            )

        while True:
            for process in processes:
                code = process.poll()
                if code is not None:
                    return code
            time.sleep(0.25)
    except FileNotFoundError as error:
        print(f"Could not start development servers: {error}", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            stop(process)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)


if __name__ == "__main__":
    raise SystemExit(main())
