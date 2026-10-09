#!/usr/bin/env python
"""Local task runner for machines without Docker/make (mirrors the Makefile).

    python dev.py setup          # create backend/.venv, install Python + npm deps, Playwright browsers
    python dev.py pg             # start the embedded Postgres 16 (pgserver) and print DATABASE_URL
    python dev.py migrate | seed | ingest | labels | train | fsc-report | followups
    python dev.py backfill --fsc 5305,5310 --start 2023-01-01 --end today
    python dev.py api            # FastAPI on :8000 (reload)
    python dev.py web            # Next.js dev server on :3000
    python dev.py test           # backend + ML pytest, frontend vitest
    python dev.py e2e            # Playwright end-to-end (starts api + web if needed)
    python dev.py lint

DATABASE_URL: if unset, an embedded PostgreSQL 16 is started in data/pg via `pgserver`
(pip-installed into backend/.venv). Redis is optional locally: jobs run in-process without it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WIN = os.name == "nt"
VENV_PY = BACKEND / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
NPM = "npm.cmd" if IS_WIN else "npm"
NPX = "npx.cmd" if IS_WIN else "npx"


def _py() -> str:
    return str(VENV_PY if VENV_PY.exists() else sys.executable)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(ROOT / "ml")])
    if not env.get("DATABASE_URL"):
        out = subprocess.run(
            [_py(), str(BACKEND / "scripts" / "localpg.py"), "dibbs"], capture_output=True, text=True, cwd=BACKEND
        )
        if out.returncode != 0:
            sys.exit(f"could not start embedded postgres:\n{out.stderr}")
        env["DATABASE_URL"] = out.stdout.strip().splitlines()[-1]
    return env


def run(cmd: list[str], cwd: Path = BACKEND, env: dict | None = None) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=cwd, env=env or os.environ.copy())


def backend_cli(args: list[str]) -> int:
    return run([_py(), "-m", "app.cli", *args], env=_env())


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    target, rest = argv[0], argv[1:]
    if target == "setup":
        if not VENV_PY.exists():
            run([sys.executable, "-m", "venv", str(BACKEND / ".venv")])
        run([_py(), "-m", "pip", "install", "-r", "requirements-dev.txt", "pgserver"])
        run([_py(), "-m", "playwright", "install", "chromium"])
        run([NPM, "install"], cwd=FRONTEND)
        return run([NPX, "playwright", "install", "chromium"], cwd=FRONTEND)
    if target == "pg":
        print(_env()["DATABASE_URL"])
        return 0
    if target in {"migrate", "seed", "ingest", "labels", "train", "fsc-report", "followups", "backfill",
                  "export-openapi", "create-superadmin"}:
        return backend_cli([target, *rest])
    if target == "api":
        env = _env()
        env.setdefault("FRONTEND_ORIGIN", "http://localhost:3000")
        backend_cli(["migrate"])
        return run([_py(), "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000", *rest], env=env)
    if target == "web":
        env = os.environ.copy()
        env.setdefault("BACKEND_URL", "http://127.0.0.1:8000")
        return run([NPM, "run", "dev"], cwd=FRONTEND, env=env)
    if target == "test":
        rc = run([_py(), "-m", "pytest", *rest], env={**os.environ, "PYTHONPATH": str(ROOT / "ml")})
        rc |= run([NPM, "run", "test:unit"], cwd=FRONTEND)
        return rc
    if target == "e2e":
        return run([NPX, "playwright", "test", *rest], cwd=FRONTEND)
    if target == "lint":
        rc = run([_py(), "-m", "ruff", "check", "app", "tests", "../ml"])
        rc |= run([NPM, "run", "lint"], cwd=FRONTEND)
        rc |= run([NPM, "run", "typecheck"], cwd=FRONTEND)
        return rc
    if target == "api-types":
        backend_cli(["export-openapi", "--out", str(FRONTEND / "openapi.json")])
        return run([NPM, "run", "gen:api"], cwd=FRONTEND)
    print(f"unknown target {target!r}\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
