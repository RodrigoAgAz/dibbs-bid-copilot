"""Start an embedded PostgreSQL 16 (pgserver) for local development without Docker.

Usage:  python scripts/localpg.py [dbname]      -> prints a DATABASE_URL

The server keeps running while the process that started it is alive; pass `--hold` to keep it
running in the foreground (Ctrl+C to stop).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def start(dbname: str = "dibbs", data_dir: Path | None = None) -> str:
    import pgserver

    srv = pgserver.get_server(str(data_dir or (REPO / "data" / "pg")), cleanup_mode=None)
    exists = srv.psql(f"SELECT 1 FROM pg_database WHERE datname = '{dbname}';")
    if "1 row" not in exists:
        srv.psql(f'CREATE DATABASE "{dbname}";')
    uri = srv.get_uri(dbname)
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    url = start(args[0] if args else "dibbs")
    print(url, flush=True)
    if "--hold" in sys.argv:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
