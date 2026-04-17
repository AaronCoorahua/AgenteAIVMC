"""
Cliente HTTP para HERALD Feed API (feed.vmcsubastas.com).
Autenticación: Bearer JWT en HERALD_API_TOKEN.
Documentación: GET {base}/openapi.json
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import requests

from src.core.logger import log_error, log_event

DEFAULT_BASE = "https://feed.vmcsubastas.com"
TIMEOUT_S = 10


def _base_url() -> str:
    return (os.getenv("HERALD_BASE_URL") or DEFAULT_BASE).rstrip("/")


def _token() -> str | None:
    t = (os.getenv("HERALD_API_TOKEN") or "").strip()
    return t or None


def _headers() -> dict[str, str]:
    token = _token()
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def is_configured() -> bool:
    return _token() is not None


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not _token():
        return None
    url = _base_url() + path
    try:
        r = requests.get(url, headers=_headers(), params=params or {}, timeout=TIMEOUT_S)
        if r.status_code == 401:
            log_error("herald_http", message="401 unauthorized", path=path)
            return None
        if r.status_code == 429:
            log_error("herald_http", message="429 rate limited", path=path)
            return None
        if r.status_code == 404:
            return None
        if r.status_code >= 500:
            log_error("herald_http", message=f"server error {r.status_code}", path=path)
            return None
        if not r.ok:
            log_error("herald_http", message=f"http {r.status_code}", path=path)
            return None
        return r.json()
    except requests.RequestException as e:
        log_error("herald_http", message=str(e), path=path)
        return None


def get_lots(
    *,
    page: int = 1,
    limit: int = 25,
    status: str | None = None,
    make: str | None = None,
    category: str | None = None,
    seller: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    sort: str | None = None,
    order: str | None = None,
) -> dict[str, Any] | None:
    params: dict[str, Any] = {"page": page, "limit": min(limit, 100)}
    if status:
        params["status"] = status
    if make:
        params["make"] = make
    if category:
        params["category"] = category
    if seller:
        params["seller"] = seller
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if sort:
        params["sort"] = sort
    if order:
        params["order"] = order
    out = _get("/herald/v1/lots", params=params)
    if out is not None:
        log_event("herald_lots_ok", page=page, limit=params["limit"])
    return out


def get_lot(lot_id: int) -> dict[str, Any] | None:
    path = f"/herald/v1/lots/{int(lot_id)}"
    out = _get(path)
    if out is not None:
        log_event("herald_lot_ok", lot_id=lot_id)
    return out


def get_market_coverage() -> dict[str, Any] | None:
    out = _get("/herald/v1/market/coverage")
    if out is not None:
        log_event("herald_market_coverage_ok")
    return out


def get_market_make(make: str) -> dict[str, Any] | None:
    m = quote(make.strip(), safe="")
    return _get(f"/herald/v1/market/{m}")


def get_market_make_model(make: str, model: str) -> dict[str, Any] | None:
    mk = quote(make.strip(), safe="")
    mo = quote(model.strip(), safe="")
    return _get(f"/herald/v1/market/{mk}/{mo}")


def get_market_make_model_year(make: str, model: str, year: int) -> dict[str, Any] | None:
    mk = quote(make.strip(), safe="")
    mo = quote(model.strip(), safe="")
    return _get(f"/herald/v1/market/{mk}/{mo}/{int(year)}")
