"""
Descarga el Centro de Ayuda web completo y lo convierte a Markdown + chunks RAG.

Flujo:
  1. Lee <script id="search-data"> de la home -> lista de URLs de articulo.
  2. Descarga CADA articulo (contenido server-rendered, sin JS).
  3. Parsea la estructura h1 (articulo) / h2 (pregunta) y el bloque
     "Respuesta rapida", que NO estan en el JSON de busqueda.
  4. Escribe un .md por articulo en data/helpcenter/ (para revision humana)
     y un JSON de chunks en data/processed/ (para Pinecone).

Uso:
    python -m src.ingest.parse_helpcenter_web
"""
import json
import re
import sys
import time
import unicodedata
import urllib.request
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

BASE_URL = "https://centro-de-ayuda-vmc.vercel.app"
DOCS_DIR = ROOT / "data" / "helpcenter"
OUT_PATH = ROOT / "data" / "processed" / "helpcenter_web_chunks.json"

SEARCH_DATA_RE = re.compile(
    r'<script[^>]*id="search-data"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)
MAIN_RE = re.compile(r"<main[^>]*>(.*?)</main>", re.DOTALL | re.IGNORECASE)
DROP_RE = re.compile(r"<(script|style|svg|nav|footer)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
NUM_RE = re.compile(r"\d")

# Secciones de navegacion/CTA que se repiten en cada pagina y no son contenido.
NOISE = (
    "¿Tienes otras consultas?",
    "Habla con nosotros",
    "Artículos de esta categoría",
    "Volver al inicio",
    "Centro de Ayuda",
)

# Anclas sin significado propio ("AQUI", "click aqui"...): sin la URL la frase
# queda coja, asi que solo en esos casos conservamos el destino.
VAGUE_ANCHOR_RE = re.compile(
    r"^(¡?\s*(ingresa|click|clic|haz clic|entra|dale)?\s*"
    r"(aqui|aquí|acá|aca|here)\s*!?|ver m[áa]s|m[áa]s informaci[óo]n|link|enlace)$",
    re.IGNORECASE,
)

NBSP = " "
ZWSP = "​"


# --- Correcciones de contenido sobre la fuente ---------------------------
# El Centro de Ayuda publica dos cifras distintas para el Fee por uso de
# pasarela: 3.9% en el articulo dedicado y 5.90% (via Niubiz) en el de
# SubasCoins. VMC confirmo que la cifra correcta es 3.9%, asi que la
# normalizamos aqui para que el bot no de respuestas contradictorias segun
# que chunk recupere. Si el sitio corrige el texto, estas reglas dejan de
# aplicar solas (no encontraran coincidencia).
CONTENT_FIXES = (
    (
        re.compile(
            r"A partir del 01 de agosto,?\s*las adquisiciones de SubasCoins "
            r"realizadas a trav[ée]s de la pasarela de pagos Niubiz tendr[áa]n "
            r"un recargo adicional",
            re.IGNORECASE,
        ),
        "Las adquisiciones de SubasCoins realizadas con tarjeta a través de la "
        "pasarela de pagos tendrán un recargo adicional",
    ),
    (
        re.compile(r"Si usas la pasarela Niubiz se aplica un recargo", re.IGNORECASE),
        "Si adquieres SubasCoins con tarjeta se aplica el Fee por uso de pasarela",
    ),
    (re.compile(r"5[.,]90?\s*%"), "3.9%"),
)


def apply_content_fixes(txt: str) -> str:
    for pattern, replacement in CONTENT_FIXES:
        txt = pattern.sub(replacement, txt)
    return txt


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (VMC-Bot ingest)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def slugify(value: str) -> str:
    norm = unicodedata.normalize("NFKD", value)
    norm = "".join(ch for ch in norm if not unicodedata.combining(ch))
    norm = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return (norm or "sin-titulo")[:80]


def _inline_links(fragment: str) -> str:
    """
    Convierte <a href="U">texto</a> en "texto (U)" SOLO si el texto del ancla
    es vago. Con un ancla descriptiva la URL no aporta significado al embedding
    y solo mete ruido, asi que se descarta y se conserva el texto.
    """
    def repl(m: re.Match) -> str:
        href = (m.group(1) or "").strip()
        label = re.sub(r"<[^>]+>", " ", m.group(2))
        label = re.sub(r"\s+", " ", unescape(label)).strip()
        if not href or href.startswith(("#", "javascript:")):
            return f" {label} "
        if href.startswith("/"):
            href = f"{BASE_URL}{href}"
        if label and VAGUE_ANCHOR_RE.match(label):
            return f" {label}: {href} "
        return f" {label} "

    return re.sub(
        r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        repl,
        fragment,
        flags=re.DOTALL | re.IGNORECASE,
    )


def tidy(txt: str) -> str:
    """
    Normaliza el espaciado. El sitio abre/cierra <strong> a mitad de frase,
    lo que al quitar etiquetas deja espacios sueltos antes de la puntuacion
    ("Subascoin ." o "5.90 %").
    """
    txt = txt.replace(NBSP, " ").replace(ZWSP, "")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r" *\n *", "\n", txt)
    txt = re.sub(r"\s+\.\.\.", "...", txt)
    txt = re.sub(r"(\d)\s+%", r"\1%", txt)
    txt = re.sub(r"\s+([,.;:!?%\)\]])", r"\1", txt)
    txt = re.sub(r"([\(\[¿¡])\s+", r"\1", txt)
    txt = re.sub(r"([“«])\s+", r"\1", txt)
    txt = re.sub(r"\s+([”»])", r"\1", txt)
    txt = re.sub(r"(US\$)(\d)", r"\1 \2", txt)
    txt = re.sub(r"\s+:\s*(https?://)", r": \1", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return apply_content_fixes(txt.strip())


def tag_text(fragment: str) -> str:
    """HTML -> texto plano, conservando espacios entre elementos."""
    txt = DROP_RE.sub(" ", fragment)
    txt = _inline_links(txt)
    txt = re.sub(r"<br\s*/?>", "\n", txt, flags=re.IGNORECASE)
    txt = re.sub(r"</(p|div|li|h[1-6])>", "\n", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<li[^>]*>", "\n- ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = unescape(txt)
    return tidy(txt)


def parse_article(html: str, url: str) -> dict | None:
    """Extrae titulo, respuesta rapida y pares pregunta/respuesta de un articulo."""
    main = MAIN_RE.search(html)
    body = main.group(1) if main else html
    body = DROP_RE.sub(" ", body)

    # El <h1> vive en el header del documento, fuera de <main>: buscarlo
    # en el HTML completo, no solo en el cuerpo del articulo.
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", DROP_RE.sub(" ", html), re.DOTALL | re.IGNORECASE)
    title = tag_text(h1.group(1)) if h1 else ""
    if not title:
        return None

    # Trocear por <h2>: cada uno abre una pregunta. Lo previo al primer h2
    # contiene la "Respuesta rapida" (resumen del articulo).
    parts = re.split(r"<h2[^>]*>", body, flags=re.IGNORECASE)

    intro = tag_text(parts[0])
    intro = intro.replace(title, " ", 1)
    # El sitio antepone la etiqueta "Respuesta rápida" y un saludo del asistente.
    intro = re.sub(r"^\s*Respuesta r[áa]pida\s*", "", intro).strip()
    intro = re.sub(r"S?\s*Subast[íi]n.*$", "", intro, flags=re.DOTALL).strip()
    for noise in NOISE:
        intro = intro.replace(noise, " ")
    intro = tidy(intro)

    qas = []
    for chunk in parts[1:]:
        head, _, rest = chunk.partition("</h2>")
        question = tag_text(head)
        answer = tag_text(rest)
        if not question:
            continue
        if any(n in question for n in NOISE):
            continue
        # Cortar en el bloque de "artículos relacionados" al final del articulo.
        for noise in NOISE:
            idx = answer.find(noise)
            if idx > 0:
                answer = answer[:idx]
        answer = tidy(answer)
        if not answer:
            continue
        qas.append({"question": question, "answer": answer})

    return {"title": title, "url": url, "intro": intro, "qas": qas}


def write_markdown(article: dict, category: str) -> Path:
    lines = [f"# {article['title']}", ""]
    lines.append(f"- **Categoría:** {category}")
    lines.append(f"- **Fuente:** {article['url']}")
    lines.append(f"- **Preguntas:** {len(article['qas'])}")
    lines.append("")
    if article["intro"]:
        lines += ["## Respuesta rápida", "", article["intro"], ""]
    lines.append("---")
    lines.append("")
    for qa in article["qas"]:
        lines += [f"## {qa['question']}", "", qa["answer"], ""]

    path = DOCS_DIR / f"{slugify(article['title'])}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build_chunks(articles: list[dict]) -> list[dict]:
    """Un chunk por pregunta. El titulo del articulo da contexto al embedding."""
    chunks = []
    seen: set[str] = set()
    n = 0

    for art in articles:
        if art["intro"]:
            n += 1
            chunks.append({
                "id": f"hcweb{n}",
                "text": f"{art['title']}\n{art['intro']}",
                "topic": art["title"],
                "source_url": art["url"],
                "has_numeric_data": bool(NUM_RE.search(art["intro"])),
            })

        for qa in art["qas"]:
            key = f"{qa['question'].lower()}|{qa['answer'][:120].lower()}"
            if key in seen:
                continue
            seen.add(key)
            n += 1
            chunks.append({
                "id": f"hcweb{n}",
                "text": f"{qa['question']}\n{qa['answer']}",
                "topic": art["title"],
                "source_url": art["url"],
                "has_numeric_data": bool(NUM_RE.search(qa["answer"])),
            })

    return chunks


def main():
    print(f"Descargando home: {BASE_URL}")
    home = fetch(BASE_URL)
    match = SEARCH_DATA_RE.search(home)
    if not match:
        raise SystemExit('No se encontro <script id="search-data"> en la home.')
    entries = json.loads(match.group(1).strip())

    # Solo rutas de articulo (/categorias/<cat>/<articulo>), no las de categoria.
    seen_urls: dict[str, str] = {}
    for e in entries:
        u = (e.get("u") or "").strip()
        if u.count("/") >= 3 and u not in seen_urls:
            seen_urls[u] = (e.get("c") or "").strip()

    print(f"Articulos a descargar: {len(seen_urls)}\n")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for old in DOCS_DIR.glob("*.md"):
        old.unlink()

    articles = []
    for i, (path_url, category) in enumerate(sorted(seen_urls.items()), 1):
        url = f"{BASE_URL}{path_url}"
        try:
            html = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i:2d}/{len(seen_urls)}] ERROR {path_url}: {exc}")
            continue

        article = parse_article(html, url)
        if not article:
            print(f"[{i:2d}/{len(seen_urls)}] SIN CONTENIDO {path_url}")
            continue

        md_path = write_markdown(article, category)
        articles.append(article)
        print(f"[{i:2d}/{len(seen_urls)}] {len(article['qas']):2d} preguntas -> {md_path.name}")
        time.sleep(0.3)  # cortesia con el servidor

    chunks = build_chunks(articles)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"source": BASE_URL, "chunks": chunks}, f, ensure_ascii=False, indent=2)

    total_q = sum(len(a["qas"]) for a in articles)
    print(f"\nArticulos: {len(articles)} | Preguntas: {total_q} | Chunks: {len(chunks)}")
    print(f"Markdown: {DOCS_DIR}")
    print(f"Chunks:   {OUT_PATH}")
    print("\nRevisa los .md y luego sube con: python -m src.rag.embed_helpcenter_web")


if __name__ == "__main__":
    main()
