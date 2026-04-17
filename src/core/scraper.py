"""
src/core/scraper.py
--------------------
Scraper con fallback en cascada para VMC-Bot.

Prioridad (siempre costo cero primero):
  1. Playwright — home + todas las tiendas activas (deduplicado por ID)
  2. Firecrawl (con créditos, solo si Playwright falla)
  3. Último JSON guardado (si Firecrawl no tiene créditos)
  4. Mensaje amigable al usuario (si todo falla)

El resto del código nunca sabe qué herramienta se usó.
Solo llama a scrape_inventario() y recibe el resultado o None.
"""

import os
import json
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

from src.core.logger import log_event, log_error

load_dotenv()

# ---------------------------------------------------------------------------
# 1. Rutas y configuración
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
# En Vercel/serverless el FS bajo /var/task es solo lectura: usar /tmp.
_FALLBACK_DIR = _ROOT / "data" / "raw" / "fallback"
try:
    _FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    _FALLBACK_DIR = Path("/tmp/vmc-bot-data/fallback")
    _FALLBACK_DIR.mkdir(parents=True, exist_ok=True)

_INVENTARIO_FALLBACK = _FALLBACK_DIR / "inventory_last_known.json"

# URL principal + tiendas conocidas
# Si una tienda no está activa, Playwright la saltea sin romper la cascada
_URL_HOME     = "https://www.vmcsubastas.com/"
_URLS_TIENDAS = [
    "https://www.vmcsubastas.com/maersk.html",
    "https://www.vmcsubastas.com/pacifico.html",
    "https://www.vmcsubastas.com/subastop.html",
    "https://www.vmcsubastas.com/gruas-asociados.html",
    "https://www.vmcsubastas.com/cvcenergia.html",
    "https://www.vmcsubastas.com/santander.html",
]

# Mensaje amigable cuando todo falla — nunca inventar datos
MENSAJE_SIN_INVENTARIO = (
    "En este momento no puedo consultar el inventario de vehículos disponibles. "
    "Te recomiendo revisar directamente en vmcsubastas.com o contactar a nuestro "
    "equipo y con gusto te ayudamos. 🙏"
)

# JavaScript para extraer cards — reutilizado en todas las URLs
_JS_EXTRAER_CARDS = """
    () => {
        const items = document.querySelectorAll('div[fragment]');
        return Array.from(items).map(el => {
            const linkEl   = el.querySelector('a[href*="/oferta/"]');
            const href     = linkEl?.getAttribute('href') || '';
            const fullUrl  = href ? 'https://www.vmcsubastas.com' + href : '';
            const id       = href.split('/oferta/')[1] || '';

            return {
                id:         id,
                titulo:     el.querySelector('h3.font-bold')?.innerText?.trim() || '',
                año:        el.querySelector('p.font-light')?.innerText?.trim() || '',
                precio:     el.querySelector('span.text-turquoise-900')?.innerText?.trim() || '',
                url:        fullUrl,
                imagen_url: el.querySelector('img')?.src || '',
            };
        }).filter(v => v.id !== '');
    }
"""

# JavaScript para scroll automático — fuerza lazy loading completo
_JS_SCROLL_COMPLETO = """
    async () => {
        await new Promise(resolve => {
            let total  = document.body.scrollHeight;
            let actual = 0;
            const paso = 500;
            const timer = setInterval(() => {
                window.scrollBy(0, paso);
                actual += paso;
                if (actual >= total) {
                    // Verificar si cargó más contenido tras el scroll
                    total = document.body.scrollHeight;
                    if (actual >= total) {
                        clearInterval(timer);
                        resolve();
                    }
                }
            }, 300);
        });
    }
"""


# ---------------------------------------------------------------------------
# 2. Nivel 1 — Playwright: extrae cards de una URL individual
# ---------------------------------------------------------------------------
def _extraer_cards_de_pagina(page, url: str) -> list[dict]:
    """
    Navega a una URL, hace scroll completo para activar lazy loading,
    y extrae todas las cards de vehículos.
    Si la página no tiene cards o falla, devuelve lista vacía.

    Args:
        page:  Instancia de página de Playwright (ya abierta).
        url:   URL a scrapear.

    Returns:
        Lista de vehículos encontrados (puede ser vacía).
    """
    try:
        page.goto(url, timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)

        # Intentar esperar cards — si no aparecen en 10s, la página no las tiene
        try:
            page.wait_for_selector('div[fragment]', timeout=10000)
        except Exception:
            log_event("scraper_playwright_sin_cards", url=url)
            return []

        # Scroll automático para cargar todas las cards (lazy loading)
        page.evaluate(_JS_SCROLL_COMPLETO)
        # Pausa extra para que Vue renderice lo que cargó el scroll
        page.wait_for_timeout(2000)

        vehiculos = page.evaluate(_JS_EXTRAER_CARDS)
        log_event("scraper_playwright_cards_encontradas", url=url, vehiculos=len(vehiculos))
        return vehiculos

    except Exception as e:
        log_error("scraper_playwright_pagina_error", message=str(e), url=url)
        return []


def _scrape_con_playwright() -> list[dict] | None:
    """
    Scrapea el home + todas las tiendas activas con Playwright.
    Deduplica por ID de oferta.

    Returns:
        Lista de vehículos únicos, o None si no se encontró nada.
    """
    try:
        from playwright.sync_api import sync_playwright

        todas_las_urls = [_URL_HOME] + _URLS_TIENDAS
        log_event("scraper_playwright_inicio", urls=len(todas_las_urls))

        vistos    = set()   # IDs ya agregados — para deduplicar
        resultado = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page()

            for url in todas_las_urls:
                log_event("scraper_playwright_procesando", url=url)
                cards = _extraer_cards_de_pagina(page, url)

                nuevos = 0
                for card in cards:
                    if card["id"] not in vistos:
                        vistos.add(card["id"])
                        card["fuente_url"] = url
                        resultado.append(card)
                        nuevos += 1

                log_event("scraper_playwright_nuevos",
                          url=url, nuevos=nuevos, total_acumulado=len(resultado))

            browser.close()

        if not resultado:
            log_event("scraper_playwright_vacio_total")
            return None

        log_event("scraper_playwright_exito_total", total=len(resultado))
        return resultado

    except ImportError:
        log_error("scraper_playwright_no_instalado",
                  message="Playwright no instalado. Ejecutar: pip install playwright && playwright install chromium")
        return None
    except Exception as e:
        log_error("scraper_playwright_error", message=str(e))
        return None


# ---------------------------------------------------------------------------
# 3. Nivel 2 — Firecrawl (con créditos, segundo intento)
# ---------------------------------------------------------------------------
def _scrape_con_firecrawl(url: str = _URL_HOME) -> list[dict] | None:
    """
    Intenta scrapear con Firecrawl como segundo intento.
    Solo se usa si Playwright falla completamente.

    Returns:
        Lista de vehículos como dicts, o None si falla o sin créditos.
    """
    try:
        from firecrawl import FirecrawlApp
        from src.core.firecrawl_client import scrape_seguro

        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            log_error("scraper_firecrawl_sin_key", message="FIRECRAWL_API_KEY no encontrada")
            return None

        log_event("scraper_firecrawl_inicio", url=url)
        app       = FirecrawlApp(api_key=api_key)
        contenido = scrape_seguro(app, url)

        if not contenido:
            return None

        vehiculos = [{"texto_raw": contenido, "scraped_via": "firecrawl"}]
        log_event("scraper_firecrawl_exito", url=url)
        return vehiculos

    except Exception as e:
        log_error("scraper_firecrawl_error", message=str(e), url=url)
        return None


# ---------------------------------------------------------------------------
# 4. Nivel 3 — Último JSON guardado
# ---------------------------------------------------------------------------
def _cargar_fallback() -> list[dict] | None:
    """
    Carga el último inventario scrapeado exitosamente.
    Se usa cuando tanto Playwright como Firecrawl fallan.

    Returns:
        Lista de vehículos del último scrape exitoso, o None si no existe.
    """
    if not _INVENTARIO_FALLBACK.exists():
        log_error("scraper_sin_fallback",
                  message="No hay inventario guardado previamente.")
        return None

    try:
        with open(_INVENTARIO_FALLBACK, "r", encoding="utf-8") as f:
            data = json.load(f)

        vehiculos  = data.get("vehiculos", [])
        scraped_at = data.get("scraped_at", "desconocido")

        log_event("scraper_fallback_usado",
                  scraped_at=scraped_at,
                  vehiculos=len(vehiculos))

        print(
            f"\n⚠️  Usando inventario guardado del {scraped_at}.\n"
            f"   La disponibilidad puede haber cambiado.\n"
        )
        return vehiculos

    except Exception as e:
        log_error("scraper_fallback_error", message=str(e))
        return None


def _guardar_fallback(vehiculos: list[dict], fuente: str) -> None:
    """Guarda el inventario como último scrape exitoso."""
    try:
        data = {
            "vehiculos":  vehiculos,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "fuente":     fuente,
        }
        with open(_INVENTARIO_FALLBACK, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log_event("scraper_fallback_guardado", fuente=fuente, vehiculos=len(vehiculos))
    except Exception as e:
        log_error("scraper_fallback_guardar_error", message=str(e))


# ---------------------------------------------------------------------------
# 5. Función principal — cascada completa
# ---------------------------------------------------------------------------
def scrape_inventario() -> tuple[list[dict] | None, str]:
    """
    Scrapea el inventario con fallback en cascada.

    Prioridad:
      1. Playwright — home + tiendas activas (deduplicado)
      2. Firecrawl (con créditos)
      3. Último JSON guardado
      4. None + MENSAJE_SIN_INVENTARIO

    Returns:
        Tuple (vehiculos, fuente) donde:
          - vehiculos es la lista de vehículos o None si todo falló
          - fuente indica qué método se usó: "playwright", "firecrawl",
            "fallback", o "sin_datos"
    """
    # Nivel 1 — Playwright (home + tiendas)
    log_event("scraper_cascada_inicio", home=_URL_HOME, tiendas=len(_URLS_TIENDAS))
    vehiculos = _scrape_con_playwright()
    if vehiculos:
        _guardar_fallback(vehiculos, fuente="playwright")
        return vehiculos, "playwright"

    # Nivel 2 — Firecrawl
    log_event("scraper_cascada_nivel2", razon="playwright_fallo")
    vehiculos = _scrape_con_firecrawl()
    if vehiculos:
        _guardar_fallback(vehiculos, fuente="firecrawl")
        return vehiculos, "firecrawl"

    # Nivel 3 — Último JSON guardado
    log_event("scraper_cascada_nivel3", razon="firecrawl_fallo")
    vehiculos = _cargar_fallback()
    if vehiculos:
        return vehiculos, "fallback"

    # Nivel 4 — Todo falló
    log_error("scraper_cascada_total_fail",
              message="Playwright, Firecrawl y fallback fallaron.")
    return None, "sin_datos"


def respuesta_sin_inventario() -> str:
    """
    Devuelve el mensaje amigable cuando todo el scraping falla.
    Para usar en el servidor cuando scrape_inventario() devuelve None.
    """
    return MENSAJE_SIN_INVENTARIO