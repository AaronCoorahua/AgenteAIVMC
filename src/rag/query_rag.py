"""
Prueba RAG: pregunta → búsqueda en Pinecone → (opcional) respuesta con Claude.
Uso:
  python -m src.rag.query_rag "¿Qué son los SubasCoins?"
  python -m src.rag.query_rag   # modo interactivo
Requiere PINECONE_API_KEY. Con ANTHROPIC_API_KEY genera respuesta con Claude.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def _escalation_wait_message(escalation_type: str) -> str:
    """
    Retorna el mensaje de espera para el usuario según el tipo de escalación.
    Tono Subastin: español peruano, informal, máx 2 oraciones, sin lenguaje corporativo.
    """
    messages = {
        "direct_simple":   "¡Dale! Un asesor del equipo te va a escribir en unos minutos 😊",
        "doubt":           "Eso mejor te lo aclara un asesor directamente. En unos minutos alguien del equipo te escribe.",
        "confusion":       "Mira, el tema puede ser medio enredado al inicio. Un asesor te lo explica mejor — te escribe en unos minutos.",
        "pre_abandonment": "Espera, no te vayas todavía. Un asesor del equipo te escribe en unos minutos para ayudarte mejor 😊",
        "live_auction":    "¡Ya le avisé al equipo, urgente! Un asesor te contacta al toque.",
        "legal_vehicle":   "Ese tema requiere revisión detallada para no darte info incorrecta. Un asesor especializado te escribe en breve.",
        "frustrated":      "Entendido, ya le pasé tu caso al equipo. Un asesor te contacta en breve para resolverlo.",
        "legal_threat":    "Ya derivé tu caso directo al equipo especializado. Un asesor se comunica contigo en breve.",
        "b2b":             "Eso suena como algo que puede manejar nuestro equipo comercial. Un asesor te escribe en unos minutos para coordinarlo.",
    }
    return messages.get(escalation_type, messages["direct_simple"])

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from src.core.logger import log_error, log_cost, log_event, log_rag_query, log_rag_response
from src.core.resilience import call_claude_with_retry, UserFacingError, MENSAJE_OCUPADO
import anthropic as _anthropic_module

NAMESPACE = "helpcenter"
# Retrieval: menos chunks por defecto para bajar latencia (meta <3s). RAG_TOP_K=5 para preguntas complejas.
TOP_K = int(os.getenv("RAG_TOP_K", "3"))
SYSTEM_PROMPT_PATH = ROOT / "prompts" / "system_prompt_v1.md"
# Contexto RAG enviado al LLM: máx 3.000 tokens (~4 chars/token) para reducir input y latencia.
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "3000"))
_DEFAULT_CONTEXT_CHARS = MAX_CONTEXT_TOKENS * 4
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", str(_DEFAULT_CONTEXT_CHARS)))

# Cliente singleton — instanciado una vez, reutilizado en todas las llamadas
_claude_client = None

def _get_claude_client():
    global _claude_client
    if _claude_client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        _claude_client = _anthropic_module.Anthropic(api_key=api_key)
    return _claude_client


def get_index():
    api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX_NAME", "vmc-bot-rag")
    if not api_key:
        raise SystemExit("Falta PINECONE_API_KEY en .env")
    from pinecone import Pinecone
    pc = Pinecone(api_key=api_key)
    return pc.Index(index_name)


def search(index, question: str, top_k: int | None = None):
    """Busca en Pinecone por texto; devuelve lista de chunks con score. top_k por defecto TOP_K."""
    k = top_k if top_k is not None else TOP_K
    resp = index.search(
        namespace=NAMESPACE,
        query={"inputs": {"text": question}, "top_k": k},
        fields=["text", "topic", "source_url"],
    )
    result = getattr(resp, "result", None) or getattr(resp, "result", resp)
    hits = getattr(result, "hits", None) or getattr(resp, "matches", []) or []
    out = []
    for h in hits:
        hid = getattr(h, "_id", None) or getattr(h, "id", "")
        score = getattr(h, "_score", None) or getattr(h, "score", 0)
        score = float(score) if score is not None else 0.0
        fields = getattr(h, "fields", None) or {}
        if hasattr(fields, "get"):
            text = fields.get("text", "")
            topic = fields.get("topic", "")
            source_url = fields.get("source_url", "")
        else:
            text = getattr(fields, "text", "")
            topic = getattr(fields, "topic", "")
            source_url = getattr(fields, "source_url", "")
        if not text:
            continue
        out.append({
            "id": hid,
            "score": score,
            "text": text,
            "topic": topic,
            "source_url": source_url,
        })
    return out


WHATSAPP_MAX_MESSAGE_CHARS = 4096


def trim_response_to_safe_length(text: str, max_chars: int = WHATSAPP_MAX_MESSAGE_CHARS) -> str:
    """
    Corta el texto en un límite seguro sin partir palabras ni oraciones.
    Busca el último . ? ! antes del límite; si no hay, el último espacio.
    Nunca devuelve una respuesta que termine en una palabra cortada.
    """
    if not text or len(text) <= max_chars:
        return text
    segment = text[: max_chars + 1]
    for sep in (".", "?", "!", "。"):
        last = segment.rfind(sep)
        if last >= 0:
            return text[: last + 1].strip()
    last_space = segment.rfind(" ")
    if last_space >= 0:
        return text[:last_space].strip()
    return text[:max_chars].strip()


def build_context(matches, live_block: str | None = None):
    """Convierte los chunks en un solo texto para el LLM, limitando el tamaño total del contexto."""
    parts: list[str] = []
    total_len = 0
    if live_block:
        live_text = "[Fuente en tiempo real — precios y planes actuales: https://www.vmcsubastas.com/subaspass]\n" + live_block
        if MAX_CONTEXT_CHARS > 0:
            live_text = live_text[:MAX_CONTEXT_CHARS]
        parts.append(live_text)
        total_len += len(live_text)
    if not matches:
        return "\n\n---\n\n".join(parts) if parts else ""
    for i, m in enumerate(matches, 1):
        chunk_text = f"[Fragmento {i}] (tema: {m.get('topic', '')})\n{m.get('text', '')}"
        if MAX_CONTEXT_CHARS > 0:
            remaining = MAX_CONTEXT_CHARS - total_len
            if remaining <= 0:
                break
            if len(chunk_text) > remaining:
                chunk_text = chunk_text[:remaining]
            parts.append(chunk_text)
            total_len += len(chunk_text)
        else:
            parts.append(chunk_text)
    return "\n\n---\n\n".join(parts)


def _is_subaspass_question(question: str) -> bool:
    q = (question or "").lower().strip()
    if not q:
        return False
    keywords = ("subaspass", "subas pass", "pase", "membresía", "membresia", "planes subas", "precio subas", "cuánto cuesta el pase", "costo subaspass")
    return any(k in q for k in keywords)


# --- top_k dinámico: proceso (5) vs definición (3) ---
PROCESO_KEYWORDS = (
    "qué hago", "que hago", "cómo", "como", "pasos", "qué pasa si", "que pasa si",
    "qué sigue", "que sigue", "después de", "despues de", "para qué sirve",
    "cuándo", "cuando", "requisitos", "documentos", "condiciones",
    "sanciones", "devolución", "devolucion", "pedir devolución", "pedir devolucion",
)


def get_top_k(query: str) -> int:
    """
    Clasificación simple por keywords: si la pregunta implica proceso/flujo/condiciones
    → top_k=5; si no → top_k=3 (definición o dato puntual).
    """
    if not (query or "").strip():
        return int(os.getenv("RAG_TOP_K", "3"))
    q = query.lower().strip()
    return 5 if any(kw in q for kw in PROCESO_KEYWORDS) else 3


# --- PASO 3: multi-query condicional ---
def _needs_multi_query(q: str) -> bool:
    """
    Multi-query solo vale la pena en preguntas cortas o ambiguas (≤5 palabras).
    Preguntas largas y específicas ya tienen suficiente señal para Pinecone.
    Ahorro: elimina ~1 llamada Haiku por cada FAQ de más de 5 palabras.
    """
    return len(q.split()) <= 5


def _usage_to_dict(usage) -> dict:
    if usage is None:
        return {"input": 0, "output": 0, "cached_read": 0, "cached_creation": 0}
    return {
        "input":            getattr(usage, "input_tokens", 0) or 0,
        "output":           getattr(usage, "output_tokens", 0) or 0,
        "cached_read":      getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cached_creation":  getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def answer_with_claude(question: str, context: str, history: list[dict] | None = None) -> str:
    text, *_ = answer_with_claude_with_debug(question, context, history=history)
    return text


def answer_with_claude_with_debug(
    question: str,
    context: str,
    history: list[dict] | None = None,
) -> tuple[str, dict, int, str, str]:
    """Retorna (texto_respuesta, tokens_dict, latency_ms, system_prompt_used, rag_context_used)."""
    import time

    client = _get_claude_client()
    if not client:
        return "", _usage_to_dict(None), 0, "", context

    # PASO 5: dos bloques separados.
    # Bloque 1: system prompt completo (fijo, se cachea).
    # Bloque 2: contexto RAG (varía por pregunta, no se cachea).
    # Nota: usamos el archivo completo sin recortar por "## Texto del prompt"
    # para asegurar que supera el mínimo de 1,024 tokens que exige Anthropic.
    system_raw = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") if SYSTEM_PROMPT_PATH.exists() else ""
    system_blocks = [
        {
            "type": "text",
            "text": system_raw + "\n\nCuando respondas, usa SOLO la información del siguiente contexto del Centro de Ayuda de VMC. No inventes nada.",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "Contexto:\n" + context,
        },
    ]

    messages = []
    if history:
        for turn in history:
            r = turn.get("role", "")
            c = turn.get("content", "")
            if r in ("user", "assistant") and c:
                messages.append({"role": r, "content": c})
    messages.append({"role": "user", "content": question})

    try:
        t0  = time.perf_counter()
        msg = call_claude_with_retry(
            client=client,
            model="claude-3-5-sonnet-20241022",
            max_tokens=300,  # PASO 4: reducido de 500 → 300 (respuestas WhatsApp deben ser concisas)
            system=system_blocks,  # PASO 5: lista de bloques en vez de string plano
            messages=messages,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        usage  = getattr(msg, "usage", None)
        tokens = _usage_to_dict(usage)
        block  = msg.content[0] if msg.content else None
        text   = block.text if block and hasattr(block, "text") else str(msg.content)
        text   = trim_response_to_safe_length(text)

        log_cost(
            model="claude-3-5-sonnet-20241022",
            tokens_in=tokens.get("input", 0),
            tokens_out=tokens.get("output", 0),
        )
        log_rag_response(response_preview=text, model="claude-3-5-sonnet-20241022")

        system_full = system_blocks[0]["text"] + "\n\nContexto:\n" + context
        return text, tokens, latency_ms, system_full, context

    except UserFacingError as e:
        system_full = system_blocks[0]["text"] + "\n\nContexto:\n" + context
        log_error("query_rag_user_facing", message=str(e))
        mensaje = trim_response_to_safe_length(e.mensaje)
        return mensaje, _usage_to_dict(None), 0, system_full, context

    except Exception as e:
        system_full = system_blocks[0]["text"] + "\n\nContexto:\n" + context
        err_text = str(e)
        log_error("query_rag_claude_error", message=err_text, question=question[:80])
        return MENSAJE_OCUPADO, _usage_to_dict(None), 0, system_full, context


def main():
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        question = input("Pregunta sobre VMC Subastas: ").strip()
    if not question:
        print("Escribe una pregunta.")
        return

    top_k = get_top_k(question)
    log_event("rag_topk", query=question[:120], top_k=top_k)
    index = get_index()
    if os.getenv("ANTHROPIC_API_KEY"):
        matches = search_multi_query_rrf(index, question, top_k_per_query=top_k)
        print("(Usando multi-query + RRF)")
    else:
        matches = search(index, question, top_k=top_k)
    if not matches:
        print("No se encontraron fragmentos relevantes en Pinecone.")
        return

    print("\n--- Chunks recuperados (top {}) ---\n".format(len(matches)))
    for i, m in enumerate(matches, 1):
        sc = m.get("score")
        sc = float(sc) if sc is not None else 0.0
        print(f"[{i}] score={sc:.3f} | {m.get('topic', '')}")
        print(m.get("text", "")[:400] + ("..." if len(m.get("text", "")) > 400 else ""))
        print()

    context = build_context(matches)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        print("--- Respuesta (Claude + RAG) ---\n")
        answer = answer_with_claude(question, context)
        print(answer)
    else:
        print("(Para generar respuesta con Claude, añade ANTHROPIC_API_KEY en .env)")


def search_by_queries_rrf(index, queries: list[str], top_k_per_query: int | None = None):
    from src.rag.rrf import reciprocal_rank_fusion
    k = top_k_per_query if top_k_per_query is not None else TOP_K
    list_of_results = []
    for q in (queries or []):
        if not q:
            continue
        hits = search(index, q, top_k=k)
        if hits:
            list_of_results.append(hits)
    if not list_of_results:
        return []
    fused = reciprocal_rank_fusion(list_of_results, id_key="id")
    return fused[:k]


def search_multi_query_rrf(index, question: str, top_k_per_query: int | None = None):
    from src.rag.multi_query import generate_multi_queries
    k = top_k_per_query if top_k_per_query is not None else TOP_K
    if _needs_multi_query(question):
        queries = generate_multi_queries(question, num_queries=2)
    else:
        queries = [question]
    result = search_by_queries_rrf(index, queries, top_k_per_query=k)
    if not result:
        return search(index, question)
    return result


def search_multi_query_rrf_with_debug(index, question: str, top_k_per_query: int | None = None) -> tuple:
    import time
    from src.rag.multi_query import generate_multi_queries_with_debug
    from src.rag.rrf import reciprocal_rank_fusion
    k = top_k_per_query if top_k_per_query is not None else TOP_K
    if _needs_multi_query(question):
        queries, _, _ = generate_multi_queries_with_debug(question, num_queries=2)
    else:
        queries = [question]
    t0 = time.perf_counter()
    list_of_results = []
    for q in queries:
        if not q:
            continue
        hits = search(index, q, top_k=k)
        if hits:
            list_of_results.append(hits)
    if not list_of_results:
        fallback = search(index, question, top_k=k)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return fallback, queries, [], latency_ms
    fused = reciprocal_rank_fusion(list_of_results, id_key="id")
    fused = fused[:k]
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return fused, queries, list_of_results, latency_ms


def ask_rag(question: str, use_multi_query: bool = True, history: list[dict] | None = None, top_k: int | None = None):
    question = (question or "").strip()
    if not question:
        return [], ""
    if top_k is None:
        top_k = get_top_k(question)
    log_event("rag_topk", query=question[:120], top_k=top_k)
    index = get_index()
    if use_multi_query and os.getenv("ANTHROPIC_API_KEY") and _needs_multi_query(question):
        matches = search_multi_query_rrf(index, question, top_k_per_query=top_k)
    else:
        matches = search(index, question, top_k=top_k)

    live_block = None
    subaspass_chunk = None
    if _is_subaspass_question(question):
        try:
            from src.rag.live_source import fetch_subaspass_live
            live_text, live_url = fetch_subaspass_live()
            if live_text:
                live_block = live_text[:12000]
                subaspass_chunk = {
                    "id": "live-subaspass",
                    "score": 1.0,
                    "topic": "SubasPass (tiempo real)",
                    "text": live_text[:2000] + ("..." if len(live_text) > 2000 else ""),
                    "source_url": live_url,
                }
        except Exception:
            pass

    if not matches and not live_block:
        return [], "No se encontraron fragmentos relevantes."

    context = build_context(matches, live_block=live_block)
    answer = answer_with_claude(question, context, history=history) if os.getenv("ANTHROPIC_API_KEY") else ""
    chunks_out = []
    if subaspass_chunk:
        chunks_out.append(subaspass_chunk)
    for m in matches:
        sc = m.get("rrf_score") if m.get("rrf_score") is not None else m.get("score", 0)
        chunks_out.append({
            "id": m.get("id", ""),
            "score": round(float(sc), 3),
            "topic": m.get("topic", ""),
            "text": m.get("text", ""),
            "source_url": m.get("source_url", ""),
        })
    return chunks_out, answer


def _last_assistant_message(history: list[dict] | None) -> str | None:
    if not history:
        return None
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            return (turn.get("content") or "").strip() or None
    return None


def ask_with_router(question: str, use_multi_query: bool = True, history: list[dict] | None = None):
    question = (question or "").strip()
    if not question:
        return [], "", "faq"
    last_bot = _last_assistant_message(history)
    try:
        from src.rag.router import classify_intent, get_top_k_for_intent
        intent = classify_intent(question, last_assistant_message=last_bot)
    except Exception:
        intent = "faq"
    if intent == "faq":
        top_k = get_top_k_for_intent(intent, question)
        chunks, answer = ask_rag(question, use_multi_query=use_multi_query, history=history, top_k=top_k)
        return chunks, answer, "faq"
    if intent == "stock_search":
        try:
            from src.rag.inventory import search_vehicles, format_stock_answer
            vehicles = search_vehicles(question)
            answer = format_stock_answer(question, vehicles)
            chunks = [
                {
                    "id": v.get("id"),
                    "score": 1.0,
                    "topic": "inventario",
                    "text": (v.get("snippet") or v.get("title") or "")[:500],
                    "source_url": v.get("url"),
                }
                for v in (vehicles or [])
            ]
            return chunks, answer, "stock_search"
        except Exception:
            msg = "Por ahora estoy aprendiendo a buscar carros en tiempo real — esa función llega pronto 🚗\n\nMientras tanto, puedes ver todo el inventario disponible directo en 👉 vmcsubastas.com. Ahí encuentras filtros por marca, modelo y precio. ¿Te ayudo con algo más sobre el proceso de subasta?"
            return [], msg, "stock_search"
    if intent == "soporte_humano":
        from src.rag.router import classify_escalation_type
        msg = _escalation_wait_message(classify_escalation_type(question))
        return [], msg, "soporte_humano"
    msg = "¡Hola! 👋 Soy Subastin, el asistente virtual de VMC Subastas. Ese tema se escapa un poco de lo que manejo, pero si tienes dudas sobre registro, SubasCoins, consignación u ofertas, aquí estoy. ¿En qué te puedo ayudar?"
    return [], msg, "fuera_dominio"


def ask_with_router_debug(question: str, history: list[dict] | None = None) -> tuple[list, str, str, dict]:
    import time
    from src.rag.router import classify_intent_with_debug
    from src.rag.multi_query import generate_multi_queries_with_debug

    question = (question or "").strip()
    if not question:
        return [], "", "faq", {"intent": "faq", "total_latency_ms": 0}

    last_bot = _last_assistant_message(history)
    total_start = time.perf_counter()
    intent, intent_latency_ms, intent_tokens, intent_explanation = classify_intent_with_debug(
        question, last_assistant_message=last_bot
    )

    debug = {
        "intent": intent,
        "intent_latency_ms": intent_latency_ms,
        "intent_tokens": intent_tokens,
        "intent_explanation": intent_explanation,
        "multi_queries": None,
        "multi_query_latency_ms": None,
        "multi_query_tokens": None,
        "retrieval": None,
        "generation": None,
    }

    if intent != "faq":
        if intent == "stock_search":
            try:
                from src.rag.inventory import search_vehicles, format_stock_answer
                vehicles = search_vehicles(question)
                msg = format_stock_answer(question, vehicles)
                debug["stock"] = {
                    "results_count": len(vehicles or []),
                    "sample": [
                        {"id": v.get("id"), "title": v.get("title"), "url": v.get("url")}
                        for v in (vehicles or [])[:5]
                    ],
                }
                debug["total_latency_ms"] = int((time.perf_counter() - total_start) * 1000)
                return [], msg, intent, debug
            except Exception as e:
                msg = "Por ahora estoy aprendiendo a buscar carros en tiempo real — esa función llega pronto 🚗\n\nMientras tanto, puedes ver todo el inventario disponible directo en 👉 vmcsubastas.com. Ahí encuentras filtros por marca, modelo y precio. ¿Te ayudo con algo más sobre el proceso de subasta?"
                debug["stock_error"] = str(e)
                debug["total_latency_ms"] = int((time.perf_counter() - total_start) * 1000)
                return [], msg, intent, debug
        elif intent == "soporte_humano":
            from src.rag.router import classify_escalation_type
            msg = _escalation_wait_message(classify_escalation_type(question))
        else:
            msg = "¡Hola! 👋 Soy Subastin, el asistente virtual de VMC Subastas. Ese tema se escapa un poco de lo que manejo, pero si tienes dudas sobre registro, SubasCoins, consignación u ofertas, aquí estoy. ¿En qué te puedo ayudar?"
        debug["total_latency_ms"] = int((time.perf_counter() - total_start) * 1000)
        return [], msg, intent, debug

    # FAQ: multi-query condicional (PASO 3)
    # Solo preguntas cortas/ambiguas (≤5 palabras) generan variaciones con Haiku.
    # Preguntas largas tienen suficiente señal para Pinecone sin variaciones.
    if _needs_multi_query(question):
        queries, mq_tokens, mq_latency_ms = generate_multi_queries_with_debug(question, num_queries=2)
    else:
        queries = [question]
        mq_tokens = {"input": 0, "output": 0, "cached_read": 0, "cached_creation": 0}
        mq_latency_ms = 0

    debug["multi_queries"] = queries
    debug["multi_query_latency_ms"] = mq_latency_ms
    debug["multi_query_tokens"] = mq_tokens

    from src.rag.router import get_top_k_for_intent
    top_k = get_top_k_for_intent(intent, question)
    debug["rag_top_k"] = top_k
    log_event("rag_topk", query=question[:120], top_k=top_k)
    index = get_index()
    t0 = time.perf_counter()
    matches = search_by_queries_rrf(index, queries, top_k_per_query=top_k)
    if not matches:
        matches = search(index, question, top_k=top_k)
    retrieval_latency_ms = int((time.perf_counter() - t0) * 1000)

    live_block = None
    subaspass_chunk = None
    if _is_subaspass_question(question):
        try:
            from src.rag.live_source import fetch_subaspass_live
            live_text, live_url = fetch_subaspass_live()
            if live_text:
                live_block = live_text[:12000]
                subaspass_chunk = {
                    "id": "live-subaspass",
                    "score": 1.0,
                    "topic": "SubasPass (tiempo real)",
                    "text": live_text[:2000] + ("..." if len(live_text) > 2000 else ""),
                    "source_url": live_url,
                }
        except Exception:
            pass

    if not matches and not live_block:
        debug["retrieval"] = {
            "chunks": [],
            "rrf_applied": bool(queries and len(queries) > 1),
            "retrieval_latency_ms": retrieval_latency_ms,
            "queries": queries,
        }
        debug["total_latency_ms"] = int((time.perf_counter() - total_start) * 1000)
        return [], "No se encontraron fragmentos relevantes.", "faq", debug

    context = build_context(matches, live_block=live_block)
    answer, gen_tokens, gen_latency_ms, system_used, rag_context_used = answer_with_claude_with_debug(
        question, context, history=history
    )

    chunks_out = []
    if subaspass_chunk:
        chunks_out.append(subaspass_chunk)
    for m in matches:
        sc = m.get("rrf_score") if m.get("rrf_score") is not None else m.get("score", 0)
        chunks_out.append({
            "id": m.get("id", ""),
            "score": round(float(sc), 3),
            "topic": m.get("topic", ""),
            "text": m.get("text", ""),
            "source_url": m.get("source_url", ""),
        })

    retrieval_chunks_debug = []
    for c in (chunks_out or [])[:10]:
        retrieval_chunks_debug.append({
            "id": c.get("id"),
            "score": c.get("score"),
            "category": c.get("topic"),
            "text_preview": (c.get("text") or "")[:100] + ("..." if len(c.get("text") or "") > 100 else ""),
        })

    debug["retrieval"] = {
        "chunks": retrieval_chunks_debug,
        "rrf_applied": bool(queries and len(queries) > 1),
        "retrieval_latency_ms": retrieval_latency_ms,
        "queries": queries,
    }
    cached = (gen_tokens.get("cached_read") or 0) + (gen_tokens.get("cached_creation") or 0)
    debug["generation"] = {
        "model": "claude-3-5-sonnet-20241022",
        "tokens": gen_tokens,
        "cached_tokens": cached,
        "latency_ms": gen_latency_ms,
        "system_prompt": system_used,
        "rag_context": rag_context_used,
        "response_text": answer,
    }
    debug["total_latency_ms"] = int((time.perf_counter() - total_start) * 1000)
    return chunks_out, answer, "faq", debug


if __name__ == "__main__":
    main()