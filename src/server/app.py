"""
Servidor web para probar el RAG en el navegador (sin WhatsApp).
GET /  → página con formulario para preguntas
POST /api/ask → { "question": "..." } → { "chunks": [...], "answer": "..." }
GET  /api/balance → { "balance_usd": X.XX }
POST /api/balance → { "action": "set"|"add", "amount": X.XX } (protegido con ADMIN_TOKEN)
Ejecutar: uvicorn src.server.app:app --reload --port 8000
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Header
from fastapi.responses import HTMLResponse, FileResponse, Response
from pydantic import BaseModel
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

load_dotenv(ROOT / ".env")

from src.core.logger import log_event, log_error, log_rag_query
from src.core.resilience import UserFacingError, FatalAPIError, MENSAJE_OCUPADO

# Mensajes cuando no hay créditos API: según si el usuario recién inicia o ya estaba en conversación
MSG_SIN_CREDITOS_INICIO = (
    "Hola, en unos minutos te atenderemos."
)
MSG_SIN_CREDITOS_EN_CONVERSACION = (
    "Disculpa, ya no te puedo seguir respondiendo. Pronto serás atendido por un asesor."
)


def _mensaje_sin_creditos(history: list) -> str:
    """Devuelve el mensaje según si hay historial (en conversación) o no (inicio)."""
    return MSG_SIN_CREDITOS_EN_CONVERSACION if history else MSG_SIN_CREDITOS_INICIO


def _notify_handoff(
    channel: str,
    session_id: str | None,
    from_number: str | None = None,
) -> None:
    """
    Registra que un usuario debe ser atendido por un asesor (sin créditos API).
    - Escribe en logs/handoff_queue.jsonl para que el asesor sepa CUÁNDO y A QUÉ CHAT ir.
    - Si HANDOFF_WEBHOOK_URL está definido, hace POST con el mismo payload (Slack, email, etc.).
    """
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "session_id": session_id or "",
        "from_number": from_number or "",
        "chat_para_asesor": from_number if channel == "whatsapp" else (session_id or "desconocido"),
    }
    try:
        handoff_file = LOG_DIR / "handoff_queue.jsonl"
        with open(handoff_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        log_error("handoff_queue_write_error", message=str(e), payload=payload)
    webhook_url = os.getenv("HANDOFF_WEBHOOK_URL", "").strip()
    if webhook_url:
        try:
            requests.post(webhook_url, json=payload, timeout=5)
        except Exception as e:
            log_error("handoff_webhook_error", message=str(e), url=webhook_url[:80])


def _notify_asesor_requested(
    channel: str,
    session_id: str | None,
    from_number: str | None = None,
    user_message: str | None = None,
) -> None:
    """
    Notifica (ej. Slack) que un usuario está pidiendo hablar con un asesor.
    Se llama cuando el router detecta intent soporte_humano.
    Configurar ASESOR_REQUEST_WEBHOOK_URL en .env (ej. Slack Incoming Webhook).
    """
    chat_id = from_number if channel == "whatsapp" else (session_id or "?")
    text = f"🔔 *Alguien solicita un asesor*\nCanal: {channel}\nChat: {chat_id}"
    if user_message:
        text += f"\nMensaje: \"{user_message[:200]}\""
    payload = {"text": text}
    try:
        queue_file = LOG_DIR / "asesor_requests.jsonl"
        with open(queue_file, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "channel": channel,
                        "session_id": session_id or "",
                        "from_number": from_number or "",
                        "user_message": (user_message or "")[:500],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception as e:
        log_error("asesor_request_log_error", message=str(e))
    webhook_url = os.getenv("ASESOR_REQUEST_WEBHOOK_URL", "").strip()
    if webhook_url:
        try:
            requests.post(webhook_url, json=payload, timeout=5)
        except Exception as e:
            log_error("asesor_request_webhook_error", message=str(e), url=webhook_url[:80])


app = FastAPI(title="VMC-Bot — Prueba RAG", version="0.1")

try:
    from src.server.maintenance import start_background_maintenance
    @app.on_event("startup")
    def _startup_maintenance() -> None:
        # En Vercel no se despliegan scripts/; no iniciar mantenimiento para evitar "No such file or directory"
        if os.environ.get("VERCEL"):
            return
        start_background_maintenance()
except Exception:
    pass

STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
# En Vercel el filesystem es read-only; usar /tmp para logs
LOG_DIR = Path("/tmp/vmc-bot-logs") if os.environ.get("VERCEL") else (ROOT / "logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
REQUEST_LOG = LOG_DIR / "requests.jsonl"
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").strip().lower() == "true"

# ---------------------------------------------------------------------------
# Saldo persistente
# Estrategia: Upstash Redis si UPSTASH_REDIS_REST_URL está en .env,
# fallback a archivo local logs/balance.json (para desarrollo sin Redis).
# ---------------------------------------------------------------------------
INITIAL_BALANCE = float(os.getenv("ANTHROPIC_INITIAL_BALANCE_USD", "5.0"))
ADMIN_TOKEN = os.getenv("BALANCE_ADMIN_TOKEN", "")  # setear en .env para proteger el endpoint
BALANCE_KEY = "vmc_bot_balance_usd"

# ---------------------------------------------------------------------------
# WhatsApp Cloud API (Meta)
# ---------------------------------------------------------------------------
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "")
WHATSAPP_API_BASE = "https://graph.facebook.com/v20.0"

# ---------------------------------------------------------------------------
# Intercom API
# ---------------------------------------------------------------------------
INTERCOM_ACCESS_TOKEN = os.getenv("INTERCOM_ACCESS_TOKEN", "")
INTERCOM_ADMIN_ID = os.getenv("INTERCOM_ADMIN_ID", "")  # ID del bot o admin para responder
INTERCOM_API_BASE = "https://api.intercom.io"


def _get_redis():
    """Devuelve cliente Upstash Redis si está configurado, None si no."""
    url = os.getenv("UPSTASH_REDIS_REST_URL", "")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
    if not url or not token:
        return None
    try:
        from upstash_redis import Redis
        return Redis(url=url, token=token)
    except Exception:
        return None


# Fallback: archivo local para desarrollo
_BALANCE_FILE = LOG_DIR / "balance.json"


def _read_balance() -> float:
    """Lee el saldo actual desde Redis o archivo local."""
    r = _get_redis()
    if r:
        try:
            val = r.get(BALANCE_KEY)
            if val is not None:
                return float(val)
            # Primera vez: inicializar
            r.set(BALANCE_KEY, str(INITIAL_BALANCE))
            return INITIAL_BALANCE
        except Exception:
            pass
    # Fallback archivo local
    try:
        if _BALANCE_FILE.exists():
            data = json.loads(_BALANCE_FILE.read_text())
            return float(data.get("balance_usd", INITIAL_BALANCE))
    except Exception:
        pass
    return INITIAL_BALANCE


def _write_balance(amount: float) -> float:
    """Escribe el saldo en Redis o archivo local. Devuelve el valor guardado."""
    amount = round(max(0.0, amount), 6)
    r = _get_redis()
    if r:
        try:
            r.set(BALANCE_KEY, str(amount))
            return amount
        except Exception:
            pass
    # Fallback archivo local
    try:
        _BALANCE_FILE.write_text(json.dumps({"balance_usd": amount}))
    except Exception:
        pass
    return amount


def _deduct_balance(cost: float) -> float:
    """Resta cost del saldo y devuelve el nuevo saldo."""
    current = _read_balance()
    new_balance = max(0.0, current - cost)
    return _write_balance(new_balance)


def send_whatsapp_text(to_number: str, text: str) -> None:
    """
    Envía un mensaje de texto simple por WhatsApp usando la Cloud API.
    Se ejecuta típicamente en background para no bloquear la respuesta HTTP.
    """
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        log_error(
            "whatsapp_send_not_configured",
            message="Faltan WHATSAPP_ACCESS_TOKEN o WHATSAPP_PHONE_NUMBER_ID",
        )
        return

    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text[:4096]},
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code >= 400:
            log_error(
                "whatsapp_send_error",
                message=f"HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
                body=resp.text[:500],
            )
    except Exception as e:
        log_error("whatsapp_send_exception", message=str(e))


def create_intercom_ticket(from_number: str, last_message: str, history: list) -> None:
    """
    Crea una conversación en el INBOX de Intercom para que el equipo humano la tome.
    Solo se llama cuando la intención es 'soporte_humano'.
    """
    if not INTERCOM_ACCESS_TOKEN:
        log_error("intercom_not_configured", message="Para escalar a Intercom se necesita INTERCOM_ACCESS_TOKEN.")
        return

    headers = {
        "Authorization": f"Bearer {INTERCOM_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Intercom-Version": "2.11"
    }

    try:
        # 1. Buscar o crear el contacto (usuario) por su número
        contact_id = None
        search_payload = {
            "query": {
                "field": "phone",
                "operator": "=",
                "value": from_number
            }
        }
        res_search = requests.post(f"{INTERCOM_API_BASE}/contacts/search", headers=headers, json=search_payload, timeout=5)
        if res_search.ok and res_search.json().get("data"):
            contact_id = res_search.json()["data"][0]["id"]
        else:
            # Crear lead si no existe
            create_payload = {"role": "lead", "phone": from_number, "name": f"WhatsApp {from_number}"}
            res_create = requests.post(f"{INTERCOM_API_BASE}/contacts", headers=headers, json=create_payload, timeout=5)
            if res_create.ok:
                contact_id = res_create.json().get("id")

        if not contact_id:
            log_error("intercom_contact_error", message="No se pudo crear/buscar el contacto en Intercom")
            return

        # 2. Formatear el historial para que el asesor entienda el contexto
        context = f"<b>Nuevo caso escalado desde WhatsApp ({from_number})</b><br><br>"
        context += "<b>Últimos mensajes:</b><br>"
        # Mostrar los últimos 4 mensajes del historial para contexto
        for msg in history[-4:]:
            role = "Usuario" if msg.get("role") == "user" else "Bot"
            context += f"• <i>{role}</i>: {msg.get('content')}<br>"
            
        context += f"<br><b>Razón de escalación (último mensaje):</b><br>{last_message}"

        # 3. Crear la conversación en el Inbox
        conv_payload = {
            "from": {
                "type": "contact",
                "id": contact_id
            },
            "body": context
        }
        res_conv = requests.post(f"{INTERCOM_API_BASE}/conversations", headers=headers, json=conv_payload, timeout=5)
        
        if res_conv.ok:
            log_event("intercom_ticket_created", phone=from_number, ticket_id=res_conv.json().get("id"))
        else:
            log_error("intercom_conv_error", message=f"HTTP {res_conv.status_code}: {res_conv.text[:200]}")

    except Exception as e:
        log_error("intercom_escalation_exception", message=str(e))


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    question: str
    skip_router: bool = False
    include_debug: bool = True
    session_id: str = ""
    # Historial enviado por el cliente (para Vercel/serverless donde la RAM no persiste entre requests)
    history: list[dict] = []


class BalanceRequest(BaseModel):
    action: str  # "set" | "add"
    amount: float


# ---------------------------------------------------------------------------
# Historial de conversación
# ---------------------------------------------------------------------------
MAX_HISTORY_TURNS = 6
_conversation_store: dict[str, list[dict]] = {}


def _get_history(session_id: str) -> list[dict]:
    return _conversation_store.get(session_id, [])


def _append_history(session_id: str, role: str, content: str) -> None:
    if not session_id:
        return
    history = _conversation_store.setdefault(session_id, [])
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY_TURNS * 2:
        _conversation_store[session_id] = history[-(MAX_HISTORY_TURNS * 2):]


def _clear_history(session_id: str) -> None:
    _conversation_store.pop(session_id, None)


def _log_request(payload: dict) -> None:
    """Escribe una línea JSON por request en logs/requests.jsonl."""
    try:
        REQUEST_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REQUEST_LOG.open("a", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")
    except Exception:
        pass
    if payload.get("ok"):
        log_rag_query(
            query=payload.get("question", ""),
            intent=payload.get("intent", ""),
            chunks_used=len(payload.get("chunks", [])),
            tokens_in=0,
            tokens_out=0,
            latency_ms=payload.get("latency_ms", 0),
        )
    else:
        log_error(
            "api_ask_error",
            message=payload.get("error", "Error desconocido"),
            question=payload.get("question", "")[:80],
            latency_ms=payload.get("latency_ms", 0),
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    """Sirve la página de prueba del RAG."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return FileResponse(html_path)
    return HTMLResponse(_fallback_html())


@app.get("/api/balance")
def get_balance():
    """Devuelve el saldo actual en USD."""
    return {"balance_usd": _read_balance()}


@app.post("/api/balance")
def update_balance(req: BalanceRequest, x_admin_token: Optional[str] = Header(default=None)):
    """
    Actualiza el saldo manualmente (cuando se agrega billing en Anthropic).
    Protegido con el header X-Admin-Token = BALANCE_ADMIN_TOKEN del .env.
    Si BALANCE_ADMIN_TOKEN está vacío, el endpoint queda abierto (solo para dev).
    """
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")
    if req.action == "set":
        new_balance = _write_balance(req.amount)
    elif req.action == "add":
        current = _read_balance()
        new_balance = _write_balance(current + req.amount)
    else:
        raise HTTPException(status_code=400, detail="action debe ser 'set' o 'add'.")
    return {"ok": True, "balance_usd": new_balance}


@app.get("/webhook/whatsapp")
def whatsapp_verify(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    """
    Endpoint de verificación para registrar el webhook en Meta.
    Meta envía hub.mode, hub.verify_token, hub.challenge como query params.
    """
    if (
        hub_mode == "subscribe"
        and hub_verify_token
        and hub_verify_token == WEBHOOK_VERIFY_TOKEN
        and hub_challenge
    ):
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación inválido.")


@app.get("/api/webhook")
def api_webhook_verify(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    """
    Verificación de Meta para la URL de webhook /api/webhook.
    Meta envía hub.mode, hub.verify_token, hub.challenge; retornamos hub.challenge como texto plano.
    """
    if (
        hub_mode == "subscribe"
        and hub_verify_token
        and hub_verify_token == WEBHOOK_VERIFY_TOKEN
        and hub_challenge
    ):
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación inválido.")


@app.post("/webhook/whatsapp")
@app.post("/api/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Recibe mensajes entrantes de WhatsApp Cloud API y responde usando el RAG.
    Por ahora maneja solo mensajes de texto.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    try:
        entry_list = body.get("entry") or []
        if not entry_list:
            return {"status": "ignored"}
        changes_list = entry_list[0].get("changes") or []
        if not changes_list:
            return {"status": "ignored"}
        value = changes_list[0].get("value") or {}
        messages = value.get("messages") or []
        if not messages:
            return {"status": "ignored"}
        message = messages[0]
        if message.get("type") != "text":
            return {"status": "ignored"}
        text = (message.get("text") or {}).get("body") or ""
        from_number = message.get("from") or ""
    except Exception as e:
        log_error("whatsapp_webhook_parse_error", message=str(e))
        return {"status": "ignored"}

    if not text or not from_number:
        return {"status": "ignored"}

    session_id = f"wa_{from_number}"
    history = _get_history(session_id)

    from src.rag.query_rag import ask_with_router
    from src.core.resilience import FatalAPIError

    try:
        _, answer, intent = ask_with_router(text, history=history)
        reply = answer or "Por ahora no puedo responder, intenta nuevamente en unos minutos."
        if intent == "soporte_humano":
            _notify_asesor_requested(
                channel="whatsapp",
                session_id=session_id,
                from_number=from_number,
                user_message=text,
            )
            # Crear el ticket en Intercom para el inbox de los agentes de soporte
            background_tasks.add_task(create_intercom_ticket, from_number, text, history)
    except FatalAPIError as e:
        reply = _mensaje_sin_creditos(history)
        intent = "error"
        _notify_handoff(channel="whatsapp", session_id=session_id, from_number=from_number)
        log_error(
            "whatsapp_fatal_no_credits",
            message=str(e),
            session_id=session_id,
            from_number=from_number,
            has_history=len(history) > 0,
        )
    except Exception as e:
        log_error("whatsapp_webhook_rag_error", message=str(e))
        reply = "En este momento estoy con problemas técnicos. Intenta escribir de nuevo en unos minutos."
        intent = "error"

    _append_history(session_id, "user", text)
    _append_history(session_id, "assistant", reply)

    background_tasks.add_task(send_whatsapp_text, from_number, reply)

    return {"status": "ok", "intent": intent}


def _client_key(request: Request) -> str:
    """Clave para rate limit: IP del cliente."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _build_debug_cost(debug: dict) -> dict:
    """Añade cost a debug a partir de intent_tokens, multi_query_tokens y generation.tokens."""
    from src.server.cost_estimate import calculate_cost
    breakdown = {}
    it = debug.get("intent_tokens") or {}
    breakdown["haiku_router"] = calculate_cost(
        "haiku",
        it.get("input", 0),
        it.get("output", 0),
        it.get("cached_read", 0) + it.get("cached_creation", 0),
    )
    mq = debug.get("multi_query_tokens") or {}
    breakdown["haiku_multiquery"] = calculate_cost(
        "haiku",
        mq.get("input", 0),
        mq.get("output", 0),
        mq.get("cached_read", 0) + mq.get("cached_creation", 0),
    )
    gen = debug.get("generation") or {}
    gt = gen.get("tokens") or {}
    cached = gen.get("cached_tokens", 0) or (gt.get("cached_read", 0) + gt.get("cached_creation", 0))
    sonnet_in = calculate_cost("sonnet", gt.get("input", 0), 0, cached)
    sonnet_out = (gt.get("output", 0) or 0) * 15.0 / 1_000_000
    breakdown["sonnet_input"] = round(sonnet_in, 6)
    breakdown["sonnet_output"] = round(sonnet_out, 6)
    total = round(sum(breakdown.values()), 6)
    return {"this_message": total, "breakdown": breakdown}


@app.post("/api/ask")
async def api_ask(req: AskRequest, request: Request):
    """Router de intención + RAG o mensaje según clasificación."""
    from src.rag.query_rag import ask_with_router, ask_rag, ask_with_router_debug
    from src.server.rate_limit import check_rate_limit
    from src.server.whatsapp_validate import validate_response
    from src.server.cost_estimate import estimate_from_request

    # Usar historial para mensajes contextuales (inicio vs en conversación)
    history = req.history if req.history else (_get_history(req.session_id) if req.session_id else [])

    # Bloquear si saldo es 0: mensaje según contexto y avisar al asesor
    current_balance = _read_balance()
    if current_balance <= 0:
        msg = _mensaje_sin_creditos(history)
        _notify_handoff(channel="api", session_id=req.session_id or None)
        return {
            "chunks": [],
            "answer": msg,
            "response": msg,
            "intent": "error",
            "ok": False,
            "balance_remaining_usd": 0.0,
        }

    # Rate limit: esperar en silencio hasta tener hueco, sin mostrar error al usuario
    client_key = _client_key(request)
    allowed, retry_after = check_rate_limit(client_key)
    while not allowed and retry_after:
        await asyncio.sleep(retry_after)
        allowed, retry_after = check_rate_limit(client_key)

    use_debug_path = DEBUG_MODE and req.include_debug and not req.skip_router
    start = time.perf_counter()

    try:
        if use_debug_path:
            chunks, answer, intent, debug = ask_with_router_debug(req.question, history=history)
            debug["cost"] = _build_debug_cost(debug)
        elif req.skip_router:
            chunks, answer = ask_rag(req.question, history=history)
            intent = "faq"
            debug = None
        else:
            chunks, answer, intent = ask_with_router(req.question, history=history)
            debug = None

        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        answer_str = answer or ""
        whatsapp_validation = validate_response(answer_str) if answer_str else None
        cost_estimate = estimate_from_request(
            question_len=len(req.question),
            answer_len=len(answer_str),
            num_chunks=len(chunks or []),
            cached=True,
        )

        # Descontar costo real del saldo
        msg_cost = 0.0
        if debug and debug.get("cost"):
            msg_cost = debug["cost"].get("this_message", 0.0)
        elif cost_estimate:
            msg_cost = cost_estimate
        balance_remaining = _deduct_balance(msg_cost)

        _log_request({
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": str(request.url.path),
            "question": req.question,
            "intent": intent,
            "skip_router": req.skip_router,
            "latency_ms": elapsed_ms,
            "chunks": [
                {
                    "id": c.get("id"),
                    "score": c.get("score"),
                    "topic": c.get("topic"),
                    "source_url": c.get("source_url"),
                }
                for c in (chunks or [])
            ],
            "answer_len": len(answer_str),
            "ok": True,
            "whatsapp_validation": whatsapp_validation,
            "cost_estimate_usd": cost_estimate,
        })

        if req.session_id:
            _append_history(req.session_id, "user", req.question)
            _append_history(req.session_id, "assistant", answer_str)

        if intent == "soporte_humano":
            _notify_asesor_requested(
                channel="api",
                session_id=req.session_id or None,
                user_message=req.question,
            )

        quick_replies = []

        out = {
            "chunks": chunks,
            "answer": answer,
            "response": answer,
            "intent": intent,
            "ok": True,
            "balance_remaining_usd": balance_remaining,  # ← frontend usa esto
            "quick_replies": quick_replies,
        }
        if debug is not None:
            out["debug"] = debug
        return out

    except HTTPException:
        raise
    except FatalAPIError as e:
        # Sin créditos API: mensaje según si el usuario ya estaba en conversación o recién inicia
        msg = _mensaje_sin_creditos(history)
        _notify_handoff(channel="api", session_id=req.session_id or None)
        log_error(
            "api_fatal_no_credits",
            message=str(e),
            session_id=req.session_id or None,
            has_history=len(history) > 0,
        )
        return {
            "chunks": [],
            "answer": msg,
            "response": msg,
            "intent": "error",
            "ok": False,
            "balance_remaining_usd": _read_balance(),
        }
    except UserFacingError as e:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        log_error(
            "api_user_facing_error",
            message=e.mensaje,
            question=req.question[:80],
            latency_ms=elapsed_ms,
        )
        return {
            "chunks": [],
            "answer": e.mensaje,
            "response": e.mensaje,
            "intent": "error",
            "ok": False,
            "balance_remaining_usd": _read_balance(),
        }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        _log_request({
            "ts": datetime.now(timezone.utc).isoformat(),
            "path": str(request.url.path),
            "question": req.question,
            "intent": None,
            "skip_router": req.skip_router,
            "latency_ms": elapsed_ms,
            "chunks": [],
            "answer_len": 0,
            "ok": False,
            "error": str(e),
        })
        raise HTTPException(status_code=500, detail=MENSAJE_OCUPADO)


@app.delete("/api/session/{session_id}")
def api_delete_session(session_id: str):
    """Limpia el historial de conversación de una sesión."""
    _clear_history(session_id)
    return {"ok": True, "session_id": session_id}


def _fallback_html():
    """HTML mínimo si no existe static/index.html."""
    return """<!DOCTYPE html><html lang="es"><head>
  <meta charset="utf-8"><title>VMC-Bot — Prueba RAG</title>
  <style>
    body { font-family: system-ui; max-width: 720px; margin: 0 auto; padding: 24px;
           background: #1a1a2e; color: #eee; }
    textarea { width: 100%; min-height: 80px; padding: 12px; border-radius: 8px;
               border: 1px solid #444; background: #16213e; color: #eee; }
    button { margin-top: 12px; padding: 12px 24px; background: #e94560; color: #fff;
             border: none; border-radius: 8px; cursor: pointer; }
    .answer { background: #16213e; padding: 16px; border-radius: 8px; margin-top: 16px; white-space: pre-wrap; }
    .error { color: #e94560; }
  </style></head><body>
  <h1>VMC-Bot</h1>
  <form id="f">
    <textarea name="q" placeholder="¿Qué son los SubasCoins?"></textarea>
    <button type="submit">Enviar</button>
  </form>
  <div id="result"></div>
  <script>
    document.getElementById("f").onsubmit = async (e) => {
      e.preventDefault();
      const q = document.querySelector('textarea[name=q]').value.trim();
      const result = document.getElementById("result");
      result.innerHTML = "<p>Cargando...</p>";
      try {
        const r = await fetch("/api/ask", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q }) });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || "Error");
        result.innerHTML = data.answer
          ? '<div class="answer">' + data.answer + '</div>'
          : "<p class='error'>Sin respuesta.</p>";
      } catch (err) {
        result.innerHTML = '<p class="error">' + err.message + "</p>";
      }
    };
  </script></body></html>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))