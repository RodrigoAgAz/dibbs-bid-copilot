"""Production server entry point: `python -m app.serve`.

Binds ONE dual-stack socket (IPv6 with IPV6_V6ONLY off, so it also accepts IPv4) on $PORT and hands it to
uvicorn. `uvicorn --host ::` is IPv6-only, because asyncio sets IPV6_V6ONLY, which breaks platforms that
health-check over IPv4 but route private traffic over IPv6 (e.g. Railway).
"""

from __future__ import annotations

import os
import socket

import uvicorn


def dual_stack_socket(port: int) -> socket.socket:
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("::", port))
    except OSError:  # no IPv6 in this environment: plain IPv4
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
    sock.listen(2048)
    return sock


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    sock = dual_stack_socket(port)
    print(f"serving on [::]:{port} (dual-stack)", flush=True)
    config = uvicorn.Config("app.main:app", proxy_headers=True, forwarded_allow_ips="*",
                            log_level=os.environ.get("LOG_LEVEL", "info").lower())
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
