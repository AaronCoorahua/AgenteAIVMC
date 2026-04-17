"""
HERALD: lotes + mercado para intención stock_search.
Prioridad sobre inventario JSON si HERALD_API_TOKEN está definido.
"""
from __future__ import annotations

import re
import traceback
from typing import Any

from src.core.herald_client import (
    get_lot,
    get_lots,
    get_market_coverage,
    get_market_make,
    get_market_make_model,
    get_market_make_model_year,
    is_configured,
)
from src.core.logger import log_error, log_event

MAX_CONTEXT_CHARS = 12_000

# Marcas reconocidas (orden: más largas primero para coincidencias correctas)
KNOWN_MAKES = (
    "mercedes benz", "mercedes-benz", "mercedes", "chevrolet", "mitsubishi",
    "volkswagen", "hyundai", "toyota", "nissan", "honda", "mazda", "kia",
    "suzuki", "ford", "bmw", "audi", "peugeot", "renault", "jeep", "changan",
    "great wall", "subaru", "volvo", "mini", "fiat", "dodge", "gmc", "isuzu",
    "jac", "dfsk", "foton", "haval", "geely", "byd", "mg", "citroën", "citroen",
)

STOPWORDS = frozenset({
    "un", "una", "unos", "unas", "el", "la", "los", "las", "de", "del", "y", "o",
    "en", "por", "para", "con", "hay", "que", "qué", "cuánto", "cuanto", "cual",
    "cuál", "tienen", "tiene", "busco", "quiero", "ver", "muestrame", "muéstrame",
    "disponible", "disponibles", "auto", "autos", "carro", "carros", "vehiculo",
    "vehículo", "camioneta", "modelo", "año", "ano", "precio", "precios",
})

_COVERAGE_PAT = re.compile(
    r"\b(cobertura|qu[ée]\s+marcas|marcas\s+(con\s+)?datos|datos\s+de\s+mercado|"
    r"mercado\s+en\s+general|listado\s+de\s+marcas|todas\s+las\s+marcas)\b",
    re.IGNORECASE,
)
_MARKET_PRICE_PAT = re.compile(
    r"\b(precio|precios|vale|valor|valen|cotiz|mercado|valoraci|avt|depreci)\b",
    re.IGNORECASE,
)


def herald_configured() -> bool:
    return is_configured()


def _truncate(s: str, max_len: int = MAX_CONTEXT_CHARS) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3].rstrip() + "..."


def _fmt_money(n: Any) -> str:
    if n is None:
        return "—"
    try:
        return f"${float(n):,.0f} USD"
    except (TypeError, ValueError):
        return str(n)


def format_lots_response(data: dict[str, Any]) -> str:
    rows: list[str] = []
    total = data.get("total", 0)
    page = data.get("page", 1)
    items = data.get("data") or []
    rows.append(
        f"Catálogo HERALD (total aproximado: {total}, página {page}, mostrando {len(items)})."
    )
    rows.append("Precios base en USD. Enlaces: usar canonical_url del lote.")
    for snap in items[:50]:
        lid = snap.get("lot_id", "")
        code = snap.get("lot_code", "")
        url = snap.get("canonical_url", "")
        make = snap.get("make", "")
        model = snap.get("model", "")
        year = snap.get("year", "")
        status = snap.get("auction_status", "")
        price = _fmt_money(snap.get("base_price_usd"))
        line = f"- [{code}]({url}) — {make} {model} {year} | {status} | base {price}"
        rows.append(line)
    return _truncate("\n".join(rows))


def format_lot_detail(snap: dict[str, Any]) -> str:
    lines = [
        f"**Lote {snap.get('lot_code', '')}** (id {snap.get('lot_id', '')})",
        f"- URL: {snap.get('canonical_url', '')}",
        f"- {snap.get('make', '')} {snap.get('model', '')} {snap.get('year', '')}",
        f"- Estado subasta: {snap.get('auction_status', '')}",
        f"- Precio base: {_fmt_money(snap.get('base_price_usd'))}",
        f"- Vendedor: {snap.get('seller_name', '')}",
        f"- Publicación: {snap.get('publication_date', '')}",
    ]
    mv = snap.get("market_valuation")
    if isinstance(mv, dict):
        lines.append("- Valoración mercado (si aplica): ver `reference_label` en JSON fuente.")
    return _truncate("\n".join(lines))


def format_market_coverage(data: dict[str, Any]) -> str:
    lines = [
        f"Cobertura de mercado HERALD: {data.get('total_makes', '?')} marcas, "
        f"{data.get('total_models', '?')} modelos referenciados.",
    ]
    note = data.get("note")
    if note:
        lines.append(str(note))
    for m in (data.get("makes") or [])[:40]:
        mk = m.get("make", "")
        mc = m.get("model_count", "")
        lc = m.get("lot_count", "")
        models = m.get("models") or []
        preview = ", ".join(str(x) for x in models[:8])
        if len(models) > 8:
            preview += "…"
        lines.append(f"- **{mk}**: {mc} modelos, {lc} lotes. Modelos: {preview}")
    return _truncate("\n".join(lines))


def format_market_make(data: dict[str, Any]) -> str:
    mk = data.get("make", "")
    lines = [
        f"Mercado HERALD — marca **{mk}**: {data.get('total_models', '?')} modelos, "
        f"{data.get('lot_count', '?')} lotes.",
    ]
    for row in (data.get("models") or [])[:30]:
        model = row.get("model", "")
        lc = row.get("lot_count", "")
        years = row.get("years_available") or []
        ys = ", ".join(str(y) for y in sorted(years)[:12])
        if len(years) > 12:
            ys += "…"
        lines.append(f"- {model} (lotes: {lc}) — años: {ys}")
    return _truncate("\n".join(lines))


def format_market_model(data: dict[str, Any]) -> str:
    mk = data.get("make", "")
    mo = data.get("model", "")
    lines = [f"Valoraciones AVT (USD) — {mk} {mo} — lotes HERALD: {data.get('lot_count', '?')}"]
    for v in (data.get("valuations") or [])[:25]:
        y = v.get("year")
        val = v.get("value") or {}
        c = val.get("conservative")
        m = val.get("market")
        o = val.get("optimistic")
        lines.append(
            f"- Año {y}: conservador {_fmt_money(c)} | mercado {_fmt_money(m)} | optimista {_fmt_money(o)}"
        )
    return _truncate("\n".join(lines))


def format_market_year(data: dict[str, Any]) -> str:
    mk = data.get("make", "")
    mo = data.get("model", "")
    y = data.get("year", "")
    val = data.get("value") or {}
    lines = [
        f"Valoración mercado **{mk} {mo} {y}** (HERALD / AVT, USD)",
        f"- Conservador: {_fmt_money(val.get('conservative'))}",
        f"- Mercado (referencia): {_fmt_money(val.get('market'))}",
        f"- Optimista: {_fmt_money(val.get('optimistic'))}",
        f"- Señal: {data.get('signal', '—')} (nivel {data.get('signal_level', '—')})",
    ]
    comp = data.get("comparables") or {}
    if comp:
        lines.append(
            f"- Comparables: VMC avg {_fmt_money(comp.get('vmc_avg'))} (n={comp.get('vmc_count', '—')}) | "
            f"ML avg {_fmt_money(comp.get('ml_avg'))} (n={comp.get('ml_count', '—')})"
        )
    att = data.get("attribution")
    if isinstance(att, dict):
        cite = att.get("citation_markdown") or att.get("reference_label") or ""
        if cite:
            lines.append(f"- Atribución: {cite}")
    return _truncate("\n".join(lines))


def _extract_year(text: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    if not m:
        return None
    y = int(m.group())
    if 1990 <= y <= 2031:
        return y
    return None


def _make_pattern(brand: str) -> str:
    parts = brand.lower().split()
    return r"\s+".join(re.escape(p) for p in parts)


def _find_make(text: str) -> str | None:
    t = text.lower()
    for brand in sorted(KNOWN_MAKES, key=len, reverse=True):
        if re.search(r"\b" + _make_pattern(brand) + r"\b", t):
            return brand
    return None


def _extract_model(text: str, make: str) -> str | None:
    t = text.lower()
    m = re.search(r"\b" + _make_pattern(make) + r"\b", t)
    if not m:
        return None
    rest = t[m.end() :]
    rest = re.sub(r"[^\w\s]", " ", rest)
    tokens: list[str] = []
    for w in rest.split():
        if not w:
            continue
        if w in STOPWORDS:
            continue
        if w.isdigit() and len(w) == 4:
            break
        tokens.append(w)
        if len(tokens) >= 4:
            break
    if not tokens:
        return None
    return " ".join(tokens).strip()


def _parse_lot_id(text: str) -> int | None:
    m = re.search(r"\b(?:oferta|lote|lot)\s*#?\s*(\d{4,})\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"\boferta\s+(\d{4,})\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _wants_coverage(text: str) -> bool:
    return bool(_COVERAGE_PAT.search(text))


def _wants_market_price_style(text: str) -> bool:
    return bool(_MARKET_PRICE_PAT.search(text))


def _chunks_from_lots(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for snap in items[:10]:
        lid = snap.get("lot_id")
        url = snap.get("canonical_url") or ""
        title = f"{snap.get('lot_code', '')} — {snap.get('make', '')} {snap.get('model', '')} {snap.get('year', '')}"
        out.append({
            "id": str(lid) if lid is not None else "",
            "score": 1.0,
            "topic": "herald_lotes",
            "text": (title + f" | base {_fmt_money(snap.get('base_price_usd'))}")[:500],
            "source_url": url,
        })
    return out


def fetch_stock_for_answer(question: str) -> tuple[str, list[dict[str, Any]], str | None]:
    """
    Devuelve (texto_para_usuario, chunks_debug, error_code).
    error_code: None si OK, o 'not_configured' | 'empty' | 'failed'
    Nunca lanza: errores de API o JSON inesperado → log + ("", [], "failed").
    """
    if not herald_configured():
        return "", [], "not_configured"

    q = (question or "").strip()
    if not q:
        return "", [], "empty"

    try:
        return _fetch_stock_for_answer_impl(q)
    except Exception as e:
        log_error(
            "herald_fetch_exception",
            message=str(e),
            traceback=traceback.format_exc()[:6000],
            question_preview=q[:200],
        )
        return "", [], "failed"


def _fetch_stock_for_answer_impl(q: str) -> tuple[str, list[dict[str, Any]], str | None]:
    """Lógica interna; puede lanzar si algo va mal (capturada arriba)."""
    # 1) Lote específico
    lot_id = _parse_lot_id(q)
    if lot_id is not None:
        snap = get_lot(lot_id)
        if snap:
            text = format_lot_detail(snap)
            url = snap.get("canonical_url") or ""
            chunks = [{
                "id": str(lot_id),
                "score": 1.0,
                "topic": "herald_lote",
                "text": (snap.get("lot_code", "") + " " + str(snap.get("make", "")))[:500],
                "source_url": url,
            }]
            log_event("herald_stock_route", route="lot_detail", lot_id=lot_id)
            return text, chunks, None
        return (
            f"No encontré el lote **{lot_id}** en HERALD. Verifica el número en vmcsubastas.com.",
            [],
            None,
        )

    year = _extract_year(q)
    make = _find_make(q)
    model = _extract_model(q, make) if make else None
    if model and year and str(year) in model:
        model = model.replace(str(year), "").strip()

    # 2) Cobertura de mercado (sin marca obligatoria)
    if _wants_coverage(q) and not make:
        data = get_market_coverage()
        if data:
            log_event("herald_stock_route", route="market_coverage")
            return format_market_coverage(data), [], None

    # 3) Marca + modelo + año → valoración año
    if make and model and year:
        data = get_market_make_model_year(make, model, year)
        if data:
            log_event("herald_stock_route", route="market_year", make=make, model=model, year=year)
            return format_market_year(data), [], None

    # 4) Marca + modelo → valoración por modelo
    if make and model:
        data = get_market_make_model(make, model)
        if data:
            log_event("herald_stock_route", route="market_model", make=make, model=model)
            return format_market_model(data), [], None

    # 5) Solo marca
    if make:
        if _wants_market_price_style(q) or re.search(
            r"\b(modelos|años|mercado|valor)\b", q, re.IGNORECASE
        ):
            data = get_market_make(make)
            if data:
                log_event("herald_stock_route", route="market_make", make=make)
                return format_market_make(data), [], None
        # Listado de lotes filtrado por marca
        data = get_lots(make=make, limit=25, page=1)
        if data and (data.get("data") or []):
            items = data.get("data") or []
            log_event("herald_stock_route", route="lots_by_make", make=make)
            return format_lots_response(data), _chunks_from_lots(items), None

    # 6) Cobertura si preguntó mezclado con otras palabras
    if _wants_coverage(q):
        data = get_market_coverage()
        if data:
            log_event("herald_stock_route", route="market_coverage_fallback")
            return format_market_coverage(data), [], None

    # 7) Catálogo general
    data = get_lots(limit=20, page=1)
    if data and (data.get("data") is not None):
        items = data.get("data") or []
        log_event("herald_stock_route", route="lots_default")
        return format_lots_response(data), _chunks_from_lots(items), None

    log_error("herald_stock", message="empty response from HERALD")
    return "", [], "failed"

