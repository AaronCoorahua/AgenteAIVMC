"""
HERALD: lotes + mercado para intención stock_search.
Prioridad sobre inventario JSON si HERALD_API_TOKEN está definido.
"""
from __future__ import annotations

import re
import traceback
import unicodedata
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
# HERALD pagina resultados; antes pedíamos 20–25 y solo veías una página.
HERALD_LOTS_PAGE_LIMIT = 100
# Máximo a listar en un mensaje (WhatsApp / contexto); el total sigue saliendo en el encabezado.
HERALD_LOTS_MAX_LINES = 80

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
    "cuál", "tienen", "tiene", "tienes", "tengo", "busco", "quiero", "ver", "muestrame", "muéstrame",
    "disponible", "disponibles", "auto", "autos", "carro", "carros", "vehiculo",
    "vehículo", "camioneta", "modelo", "año", "ano", "precio", "precios",
    # Frases típicas después del modelo (“hilux, puedes buscar…”)
    "puedes", "puede", "pueden", "podrías", "podrias", "buscar", "busca", "buscame",
    "mira", "mirá", "dime", "decir", "pregunte", "pregunta", "pregunté", "pero",
    "solo", "también", "tambien", "ayuda", "ayudame", "ayúdame", "ahora", "bien",
    # Explicaciones (“la unidad se llama…”)
    "unidad", "unidades", "llama", "llaman", "llamas", "nombre", "llamado", "llamada",
    "algun", "alguna", "algunos", "algunas", "dice", "dicen", "eso", "este", "esta",
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


def _lots_items(data: dict[str, Any]) -> list[Any]:
    """Lista de lotes desde payload HERALD; tolera `data` ausente o no-lista."""
    raw = data.get("data")
    if isinstance(raw, list):
        return raw
    return []


def _get_lots_paged(*, make: str | None = None) -> dict[str, Any] | None:
    """
    Página 1 con hasta HERALD_LOTS_PAGE_LIMIT ítems.
    Si el API indica más publicados y la página 1 viene llena, pide página 2 y concatena
    (hasta 2× límite) para acercarse a lo que el usuario ve en la web.
    """
    kw: dict[str, Any] = {"limit": HERALD_LOTS_PAGE_LIMIT, "page": 1}
    if make:
        kw["make"] = make
    first = get_lots(**kw)
    if first is None:
        return None
    items = _lots_items(first)
    total = int(first.get("total") or 0)
    if len(items) < HERALD_LOTS_PAGE_LIMIT:
        return first
    if total and total <= len(items):
        return first
    kw["page"] = 2
    second = get_lots(**kw)
    if second is None:
        return first
    items2 = _lots_items(second)
    if not items2:
        return first
    merged = {**first, "data": items + items2}
    log_event("herald_lots_merged_pages", make=make or "", page1=len(items), page2=len(items2), total=total)
    return merged


def _dedupe_query_lines(s: str) -> str:
    """Evita repetir la misma frase al unir varios mensajes (WhatsApp / debounce)."""
    parts = re.split(r"[\n\r]+", s)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        p = " ".join(p.split())
        if len(p) < 2:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return " ".join(out)


def _extract_explicit_vehicle_label(text: str) -> str | None:
    """
    Captura “se llama TOYOTA AVANZA”, “unidad se llama …”, etc. para priorizar marca/modelo.
    """
    patterns = (
        r"\b(?:la\s+)?unidad\s+se\s+llama\s+([A-Za-z0-9áéíóúÁÉÍÓÚÑñ\s\-]{2,80})",
        r"\bse\s+llama\s+([A-Za-z0-9áéíóúÁÉÍÓÚÑñ\s\-]{2,80})",
        r"\bnombre\s+(?:del\s+)?(?:modelo|vehículo|vehiculo|auto)\s*[:\s]+\s*([A-Za-z0-9áéíóúÁÉÍÓÚÑñ\s\-]{2,80})",
    )
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            lab = " ".join(m.group(1).split())
            if len(lab) >= 2:
                return lab
    return None


def _preferred_stock_query(s: str) -> str:
    """Ante aclaraciones explícitas, pone primero marca/modelo para parsear bien."""
    s = " ".join(s.split())
    label = _extract_explicit_vehicle_label(s)
    if label:
        return f"{label}. {s}"
    return s


def _stock_query_text(question: str, history: list[dict[str, Any]] | None) -> str:
    """Une últimos mensajes del usuario con la pregunta actual (marca/modelo en turnos previos)."""
    q = unicodedata.normalize("NFKC", question or "").strip()
    if not history:
        return _preferred_stock_query(_dedupe_query_lines(q))
    user_msgs = [
        unicodedata.normalize("NFKC", m.get("content", "").strip())
        for m in history
        if m.get("role") == "user" and (m.get("content") or "").strip()
    ]
    tail = user_msgs[-1:]  # solo el turno previo evita duplicar “toyota avanza” dos veces
    merged = " ".join(tail + [q]) if q else " ".join(tail)
    merged = " ".join(merged.split())
    return _preferred_stock_query(_dedupe_query_lines(merged))


def _make_matches_snap(want: str, snap_make: str) -> bool:
    w = (want or "").strip().lower()
    s = (snap_make or "").strip().lower()
    if not w:
        return True
    if not s:
        return False
    return s == w or w in s or s in w


def _filter_snapshots(
    items: list[dict[str, Any]],
    make: str | None,
    model: str | None,
) -> list[dict[str, Any]]:
    model_parts = (model or "").strip().lower().split()[:2]
    out: list[dict[str, Any]] = []
    for snap in items:
        sm = str(snap.get("make") or "").strip().lower()
        s_mod = (snap.get("model") or "").strip().lower()
        combined = f"{sm} {s_mod}".strip().lower()
        if make:
            mk = make.lower()
            if sm and not _make_matches_snap(make, sm):
                continue
            if not sm:
                if model_parts and model_parts[0] in s_mod:
                    pass
                elif mk not in combined and mk not in s_mod:
                    continue
        if model_parts:
            ok = False
            if len(model_parts) == 1:
                ok = model_parts[0] in s_mod
            else:
                joined = " ".join(model_parts)
                ok = joined in s_mod or all(p in s_mod for p in model_parts)
            if not ok and model_parts[0] in combined:
                ok = True
            if not ok:
                continue
        out.append(snap)
    return out


def _no_match_message(make: str | None, model: str | None) -> str:
    mt = (make or "").strip().title() if make else ""
    mo_raw = (model or "").strip().split()
    mo = " ".join(mo_raw[:2]).strip() if mo_raw else ""  # mostrar como máx. 2 palabras (evita basura parseada)
    if mt and mo:
        return (
            f"No encontré unidades publicadas de **{mt} {mo}** en HERALD ahora mismo. "
            "Puedes revisar más tarde en vmcsubastas.com o preguntar por otro modelo."
        )
    if mt:
        return (
            f"No encontré unidades publicadas de **{mt}** en HERALD ahora mismo. "
            "Revisa el catálogo en vmcsubastas.com o pregunta por otra marca o modelo."
        )
    return (
        "No encontré coincidencias en el listado actual. "
        "Revisa vmcsubastas.com o dime otra marca o modelo."
    )


def _wants_lots_inventory(text: str) -> bool:
    """Pregunta por existencia / stock en catálogo (priorizar lotes sobre valoración AVT)."""
    return bool(
        re.search(
            r"\b(tienen|tienes|hay|habrá|habra|buscar|busco|buscame|disponible|inventario|"
            r"stock|unidades|ofertas?|consigues|encuentras|venden|ver\s+si)\b",
            text,
            re.IGNORECASE,
        )
    )


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
    items = data.get("data") or []
    shown = min(len(items), HERALD_LOTS_MAX_LINES)
    if total and len(items) < total:
        rows.append(
            f"Listado de ofertas (HERALD: ~{total} publicados en catálogo; "
            f"este mensaje muestra {shown} de {len(items)} traídos del API)."
        )
        rows.append(
            "Si buscas más, entra a vmcsubastas.com o pregunta por marca/modelo para acotar."
        )
    else:
        rows.append(
            f"Listado de ofertas (HERALD: ~{total} publicados; mostrando {shown} en este mensaje)."
        )
    rows.append("Precios base en USD. Cada línea enlaza a la oferta en vmcsubastas.com.")
    for snap in items[:HERALD_LOTS_MAX_LINES]:
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
    # Tras la marca suele venir “modelo, …” — cortar en coma o punto y coma
    rest = rest.split(",")[0].split(";")[0]
    rest = re.sub(r"[^\w\s]", " ", rest)
    words = rest.split()
    make_tokens = set(make.lower().split())
    i = 0
    while i < len(words) and words[i] in STOPWORDS:
        i += 1
    tokens: list[str] = []
    for w in words[i:]:
        if w in STOPWORDS:
            break
        if w in make_tokens or w == make.lower():
            break
        if w.isdigit() and len(w) == 4:
            break
        tokens.append(w)
        if len(tokens) >= 2:
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


def fetch_stock_for_answer(
    question: str,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]], str | None]:
    """
    Devuelve (texto_para_usuario, chunks_debug, error_code).
    error_code: None si OK, o 'not_configured' | 'empty' | 'failed'
    Nunca lanza: errores de API o JSON inesperado → log + ("", [], "failed").
    """
    if not herald_configured():
        return "", [], "not_configured"

    q = _stock_query_text(question, history)
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
    q = unicodedata.normalize("NFKC", q or "").strip()
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

    skip_valuation = _wants_lots_inventory(q) and bool(make and model) and year is None

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

    # 4) Marca + modelo → valoración por modelo (omitir si el usuario pide inventario / “¿tienen?”)
    if make and model and not skip_valuation:
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
        # Listado por marca: si el API falla con ?make= (None) o devuelve mezcla, reintentar sin filtro
        # y aplicar marca/modelo solo en cliente — nunca caer al catálogo general con marca en la pregunta.
        data = _get_lots_paged(make=make)
        if data is None:
            log_event("herald_lots_make_query_failed", make=make, fallback="unfiltered_plus_filter")
            data = _get_lots_paged(make=None)
        if data is not None:
            raw_items = _lots_items(data)
            items = _filter_snapshots(raw_items, make, model)
            merged = {**data, "data": items}
            log_event(
                "herald_stock_route",
                route="lots_by_make",
                make=make,
                model=model or "",
                count=len(items),
                raw_count=len(raw_items),
            )
            if not items and raw_items:
                return _no_match_message(make, model), [], None
            if not items and not raw_items:
                return _no_match_message(make, model), [], None
            return format_lots_response(merged), _chunks_from_lots(items), None
        log_error(
            "herald_stock",
            message="get_lots devolvió None incluso sin filtro de marca",
            make=make,
        )
        return "", [], "failed"

    # 6) Cobertura si preguntó mezclado con otras palabras
    if _wants_coverage(q):
        data = get_market_coverage()
        if data:
            log_event("herald_stock_route", route="market_coverage_fallback")
            return format_market_coverage(data), [], None

    # 7) Catálogo general (solo si no detectamos marca; si hay marca, el bloque 5 ya respondió o falló)
    if not make:
        data = _get_lots_paged(make=None)
        if data is not None:
            items = _lots_items(data)
            merged = {**data, "data": items}
            log_event("herald_stock_route", route="lots_default", count=len(items))
            return format_lots_response(merged), _chunks_from_lots(items), None

    log_error(
        "herald_stock",
        message="empty response from HERALD — get_lots devolvió None (revisa logs herald_http: 401, 5xx o red)",
    )
    return "", [], "failed"

