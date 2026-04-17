"""
Búsqueda de vehículos en el inventario de VMC Subastas.

Lee de data/raw/inventory.json (generado por agents/inventory_scraper/scrape_inventory.py).
Si no existe el archivo, intenta obtenerlo en el momento con el scraper.
Si todo falla, devuelve el mensaje amigable definido en src/core/scraper.py.

Formato esperado de inventory.json:
{
  "metadata": {
    "scraped_at": "...",
    "fuente": "playwright|firecrawl|fallback",
    "total_vehiculos": N,
    "aviso": "..."
  },
  "vehiculos": [
    {
      "id": "...",
      "marca": "...",
      "modelo": "...",
      "año": 2020,
      "precio_base": 42000.0,
      "estado": "disponible",
      "tipo": "camioneta",
      "url": "...",
      "titulo": "...",
      "precio_raw": "...",
      "scraped_at": "...",
      "fuente": "..."
    }
  ]
}
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ✅ Ruta actualizada — ahora lee de data/raw/ donde guarda el scraper
INVENTORY_PATH_RAW       = ROOT / "data" / "raw" / "inventory.json"
# Mantener compatibilidad con el path anterior por si hay datos procesados
INVENTORY_PATH_PROCESSED = ROOT / "data" / "processed" / "inventory.json"

from src.core.logger import log_event, log_error
from src.core.scraper import respuesta_sin_inventario


# ---------------------------------------------------------------------------
# 1. Carga del inventario
# ---------------------------------------------------------------------------
def _limpiar_cache() -> None:
    """Limpia el cache para forzar recarga del inventario."""
    # Cache deshabilitado: siempre leemos desde disco para evitar
    # quedarnos con un snapshot vacío cuando el scraper se ejecuta después.
    return None


def load_inventory() -> dict | None:
    """
    Carga el inventario desde disco en memoria (con cache simple).

    Orden de prioridad:
      1. data/raw/inventory.json (generado por el scraper nuevo)
      2. data/processed/inventory.json (formato anterior, compatibilidad)
      3. Intenta scraping en el momento si ninguno existe
    """
    # Intentar path nuevo primero
    if INVENTORY_PATH_RAW.exists():
        try:
            with INVENTORY_PATH_RAW.open(encoding="utf-8") as f:
                data = json.load(f)
            # Normalizar al formato unificado
            inv = _normalizar_inventario(data)
            log_event("inventario_cargado", fuente="raw", vehiculos=len(inv.get("vehicles", [])))
            return inv
        except Exception as e:
            log_error("inventario_carga_error", message=str(e), path=str(INVENTORY_PATH_RAW))

    # Fallback al path anterior (compatibilidad)
    if INVENTORY_PATH_PROCESSED.exists():
        try:
            with INVENTORY_PATH_PROCESSED.open(encoding="utf-8") as f:
                data = json.load(f)
            inv = data
            log_event("inventario_cargado", fuente="processed", vehiculos=len(inv.get("vehicles", [])))
            return inv
        except Exception as e:
            log_error("inventario_carga_error", message=str(e), path=str(INVENTORY_PATH_PROCESSED))

    # No hay inventario — intentar scraping en el momento (solo en entornos con disco escribible)
    if os.environ.get("VERCEL"):
        # Vercel: FS de solo lectura salvo /tmp; el scraper escribe en data/ → Errno 30
        log_event("inventario_skip_scraper", reason="vercel_readonly_no_data_dir")
        return None
    log_event("inventario_no_encontrado", accion="intentando_scraping_live")
    try:
        from agents.inventory_scraper.scrape_inventory import main as run_scraper
        run_scraper()
        # Intentar cargar de nuevo tras el scraping
        if INVENTORY_PATH_RAW.exists():
            with INVENTORY_PATH_RAW.open(encoding="utf-8") as f:
                data = json.load(f)
            _CACHE = _normalizar_inventario(data)
            log_event("inventario_cargado_live", vehiculos=len(_CACHE.get("vehicles", [])))
            return _CACHE
    except Exception as e:
        log_error("inventario_scraping_live_error", message=str(e))

    return None


def _normalizar_inventario(data: dict) -> dict:
    """
    Convierte el formato nuevo (data/raw/inventory.json) al formato
    unificado que usa search_vehicles().

    Formato nuevo tiene: metadata + vehiculos[]
    Formato unificado tiene: source + total + vehicles[]
    """
    vehiculos_raw = data.get("vehiculos", [])
    metadata      = data.get("metadata", {})
    vehicles      = []

    for v in vehiculos_raw:
        # Construir search_text combinando todos los campos útiles
        search_parts = [
            v.get("marca", ""),
            v.get("modelo", ""),
            str(v.get("año", "")),
            v.get("tipo", ""),
            v.get("titulo", ""),
            v.get("precio_raw", ""),
            v.get("estado", ""),
            v.get("ubicacion", ""),
        ]
        search_text = " ".join(p for p in search_parts if p).lower()

        vehicles.append({
            "id":          v.get("id", ""),
            "url":         v.get("url", ""),
            "title":       v.get("titulo") or f"{v.get('marca', '')} {v.get('modelo', '')} {v.get('año', '')}".strip(),
            "brand":       v.get("marca", ""),
            "model":       v.get("modelo", ""),
            "year":        v.get("año", 0),
            "price":       v.get("precio_base", 0.0),
            "price_raw":   v.get("precio_raw", ""),
            "estado":      v.get("estado", "disponible"),
            "tipo":        v.get("tipo", ""),
            "ubicacion":   v.get("ubicacion", ""),
            "imagen_url":  v.get("imagen_url", ""),
            "scraped_at":  v.get("scraped_at", ""),
            "fuente":      v.get("fuente", ""),
            "search_text": search_text,
            # Compatibilidad con formato anterior
            "snippet":     v.get("titulo", ""),
        })

    return {
        "source":     metadata.get("fuente", "scraper"),
        "scraped_at": metadata.get("scraped_at", ""),
        "aviso":      metadata.get("aviso", "Información puede cambiar. Verifica en vmcsubastas.com."),
        "total":      len(vehicles),
        "vehicles":   vehicles,
    }


# ---------------------------------------------------------------------------
# 2. Búsqueda de vehículos
# ---------------------------------------------------------------------------
def search_vehicles(question: str, limit: int = 5) -> list[dict]:
    """
    Búsqueda de vehículos por palabras clave.
    Pondera marca, modelo y año con bonus adicional.

    Args:
        question: Pregunta del usuario en español natural.
        limit:    Máximo de resultados a devolver.

    Returns:
        Lista de vehículos ordenados por relevancia.
        Lista vacía si no hay inventario o no hay coincidencias.
    """
    inv = load_inventory()
    if not inv or not inv.get("vehicles"):
        return []

    vehicles = inv.get("vehicles", [])
    q        = (question or "").lower()
    tokens   = [t for t in q.replace("?", " ").replace(",", " ").split() if len(t) >= 3]

    if not tokens:
        return vehicles[:limit]

    # Sinónimos peruanos comunes para búsqueda de vehículos
    sinonimos = {
        "carro":      ["auto", "vehículo", "vehiculo", "sedan"],
        "camioneta":  ["pickup", "hilux", "4x4"],
        "auto":       ["carro", "sedan", "vehículo"],
        "maquina":    ["vehículo", "auto"],
    }

    # Expandir tokens con sinónimos
    tokens_expandidos = set(tokens)
    for t in tokens:
        if t in sinonimos:
            tokens_expandidos.update(sinonimos[t])

    scored = []
    for v in vehicles:
        haystack = (v.get("search_text") or "").lower()
        score    = 0

        for t in tokens_expandidos:
            if t in haystack:
                score += 1
            # Bonus por coincidencia exacta en campos clave
            if t == (v.get("brand") or "").lower():
                score += 3
            if t in (v.get("model") or "").lower():
                score += 2
            if t == str(v.get("year") or ""):
                score += 2
            if t in (v.get("tipo") or "").lower():
                score += 1

        # Solo incluir si hay al menos una coincidencia
        if score > 0:
            scored.append((score, v))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [v for _, v in scored[:limit]]


# ---------------------------------------------------------------------------
# 3. Formateo de respuesta
# ---------------------------------------------------------------------------
def format_stock_answer(question: str, results: list[dict]) -> str:
    """
    Construye una respuesta para el usuario a partir de los resultados.
    NUNCA inventa datos — solo usa lo que está en el inventario scrapeado.

    Args:
        question: Pregunta original del usuario.
        results:  Lista de vehículos de search_vehicles().

    Returns:
        Texto de respuesta listo para enviar al usuario.
    """
    # Obtener el inventario completo para saber si hay datos aunque no coincidan
    inv = load_inventory()
    vehicles_all = (inv or {}).get("vehicles", []) or []

    # Caso 1: no hay inventario en disco → mensaje amigable genérico
    if not vehicles_all:
        return respuesta_sin_inventario()

    # Caso 2: hay inventario pero no hay match para la búsqueda
    if not results:
        scraped_at = (inv or {}).get("scraped_at", "")
        fecha_str  = scraped_at[:10] if scraped_at else ""
        hora_str   = scraped_at[11:16] if scraped_at and len(scraped_at) >= 16 else ""

        lines: list[str] = []
        encabezado = "No encontré vehículos que calcen exacto con lo que pides"
        if fecha_str:
            if hora_str:
                encabezado += f" en el inventario actualizado al {fecha_str} {hora_str} UTC"
            else:
                encabezado += f" en el inventario actualizado al {fecha_str}"
        encabezado += ". Esto es una muestra de lo que hay ahora:"
        lines.append(encabezado)

        for v in vehicles_all[:5]:
            title = (v.get("title") or "").strip()
            url   = (v.get("url") or "").strip()
            precio = (v.get("price_raw") or "").strip()
            estado = (v.get("estado") or "").strip()

            linea = f"- {title or 'Vehículo disponible'}"
            if precio:
                linea += f" | {precio}"
            if estado and estado != "disponible":
                linea += f" | Estado: {estado}"
            if url:
                linea += f"\n  → {url}"
            lines.append(linea)

        aviso = (inv or {}).get(
            "aviso",
            "La disponibilidad puede cambiar. Verifica en vmcsubastas.com antes de decidir."
        )
        lines.append(f"\n{aviso}")
        return "\n".join(lines)

    # Caso 3: hay resultados concretos para la búsqueda
    aviso = (inv or {}).get(
        "aviso",
        "La disponibilidad puede cambiar. Verifica en vmcsubastas.com antes de decidir."
    )
    scraped_at = (inv or {}).get("scraped_at", "")
    fecha_str  = scraped_at[:10] if scraped_at else ""
    hora_str   = scraped_at[11:16] if scraped_at and len(scraped_at) >= 16 else ""

    lines = []
    encabezado = "Esto es lo que encontré en el inventario de VMC"
    if fecha_str:
        if hora_str:
            encabezado += f" (actualizado al {fecha_str} {hora_str} UTC)"
        else:
            encabezado += f" (actualizado al {fecha_str})"
    encabezado += ":"
    lines.append(encabezado)

    for v in results[:5]:
        title = (v.get("title") or "").strip()
        url   = (v.get("url") or "").strip()
        precio = (v.get("price_raw") or "").strip()
        estado = (v.get("estado") or "").strip()

        linea = f"- {title or 'Vehículo disponible'}"
        if precio:
            linea += f" | {precio}"
        if estado and estado != "disponible":
            linea += f" | Estado: {estado}"
        if url:
            linea += f"\n  → {url}"

        lines.append(linea)

    lines.append(f"\n{aviso}")
    return "\n".join(lines)