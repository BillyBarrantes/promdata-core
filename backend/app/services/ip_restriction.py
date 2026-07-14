from __future__ import annotations

import os
from ipaddress import ip_address, ip_network

from fastapi import Request
from starlette.responses import JSONResponse

_ALLOWED_CIDRS_ENV = "ALLOWED_CIDRS"


def _parse_allowed_cidrs() -> list:
    raw = os.getenv(_ALLOWED_CIDRS_ENV, "")
    if not raw:
        return []
    blocks = []
    for cidr in raw.split(","):
        cidr = cidr.strip()
        if cidr:
            try:
                blocks.append(ip_network(cidr, strict=False))
            except ValueError:
                pass
    return blocks


_ALLOWED_NETWORKS = _parse_allowed_cidrs()


def _is_ip_allowed(client_ip: str) -> bool:
    if not _ALLOWED_NETWORKS:
        return True
    try:
        addr = ip_address(client_ip)
        return any(addr in net for net in _ALLOWED_NETWORKS)
    except ValueError:
        return False


async def ip_restriction_middleware(request: Request, call_next):
    if not _ALLOWED_NETWORKS:
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    if not _is_ip_allowed(client_ip):
        return JSONResponse(
            status_code=403,
            content={"detail": "Acceso denegado: IP no autorizada."},
        )

    return await call_next(request)
