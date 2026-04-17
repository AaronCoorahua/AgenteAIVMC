"""
Router de intención: clasifica el mensaje del usuario con Claude Haiku.
Devuelve: faq | stock_search | soporte_humano | fuera_dominio
Sin LangChain; llamada directa a Anthropic API.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

INTENTS = ("faq", "stock_search", "soporte_humano", "fuera_dominio")


def _normalize(text: str) -> str:
    """Convierte a minúsculas, elimina tildes y comprime espacios."""
    text = text.lower().strip()
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"\s+", " ", text)
    return text


# ---------------------------------------------------------------------------
# Heurística Capa 1 — Alta confianza (sin llamar a Haiku)
# ---------------------------------------------------------------------------
_HIGH_CONFIDENCE_HUMAN = (
    # Peticiones directas
    "asesor", "asesora", "agente", "ejecutivo", "ejecutiva",
    "representante", "persona real", "persona de verdad",
    "humano", "humana", "alguien de verdad",
    "hablar con alguien", "hablar con una persona", "hablar con un humano",
    "quiero hablar", "necesito hablar",
    "pasame con", "pásame con", "derivame", "derívame",
    "transferme", "transfiéreme", "transfiereme",
    "comunicarme con", "comunícame con",
    "atencion humana", "atención humana", "soporte humano",
    "agente humano", "operador",
    # Escalada legal / regulatoria peruana
    "indecopi", "libro de reclamaciones", "quiero reclamar",
    "voy a reclamar", "voy a denunciar", "quiero hacer un reclamo",
    "pesimo servicio", "pésimo servicio", "mal servicio",
    "quiero mi dinero", "devuelvan mi dinero",
    # Frustración explícita con el bot
    "el bot no sirve", "el bot no me entiende", "esto no me sirve",
    "no me estas ayudando", "no me estás ayudando",
    "no me ayuda", "no me entiendes", "no te entiendo",
    "bot inutil", "bot inútil",
    # Peruanismos de escalada
    "puro floro", "me estas floreando", "me estás floreando",
    "me floreas", "tas floreando",
    # Abandono transaccional (rescate comercial)
    "da igual", "olvídalo", "olvidalo", "ya no quiero",
    "ya fue", "dejalo ahi", "déjalo ahí", "me rindo",
)

_HIGH_CONFIDENCE_HUMAN_PATTERNS = (
    # "quiero (un/una/al) asesor/agente/humano..."
    r"quiero\s+(un|una|al)\s+(asesor|asesora|agente|humano|humana|ejecutivo|ejecutiva|representante)",
    # "necesito (un/una) asesor..."
    r"necesito\s+(un|una)\s+(asesor|asesora|agente|humano|humana|ejecutivo|ejecutiva|representante)",
    # "me atiende un humano / que me atienda una persona"
    r"(me\s+atiende|que\s+me\s+atienda)\s+.*(humano|persona|asesor|agente)",
    # todo caps con queja (QUIERO, NO PUEDO, etc.)
    r"^[A-ZÁÉÍÓÚÑ\s!?]{15,}$",
    # signos de exclamación o interrogación excesivos
    r"[!?]{3,}",
)


def _looks_like_human_support(msg: str) -> bool:
    """
    Heurística rápida de alta confianza para detectar soporte_humano sin llamar a Haiku.
    Retorna True si el mensaje claramente pide un agente humano o escala emocionalmente.
    """
    if not msg:
        return False
    norm = _normalize(msg)
    if any(kw in norm for kw in _HIGH_CONFIDENCE_HUMAN):
        return True
    # Patrones regex sobre el texto original (para detectar caps, !!! etc.)
    for pattern in _HIGH_CONFIDENCE_HUMAN_PATTERNS:
        if re.search(pattern, msg.strip(), re.IGNORECASE):
            return True
    return False


# ---------------------------------------------------------------------------
# Heurística Capa 2 — Media confianza (flag para Haiku)
# ---------------------------------------------------------------------------
_MEDIUM_CONFIDENCE_HUMAN = (
    "ayuda", "auxilio", "no funciona", "no carga", "error",
    "falla", "fallo", "no puedo", "no me deja", "no me sale",
    "al toque", "urgente", "urgentemente", "rapido", "rápido",
    "ya van", "cuantas veces", "cuántas veces", "otra vez",
    "de nuevo", "sigo sin", "todavia no", "todavía no",
    "no aparece", "no veo", "no encuentro",
    "asado", "asada", "que palta", "qué palta", "que yuca", "qué yuca",
    "que piña", "qué piña", "que lenteja", "qué lenteja",
    "malísimo", "malisimo", "horrible", "terrible", "pésimo", "pesimo",
    "no me sirve", "no sirve", "no ayuda",
    "ya", "ok", "dale", "bueno",  # apatía (se evalúa con contexto)
)


def _might_need_human(msg: str) -> bool:
    """Retorna True si hay señales de media confianza que ameritan flag en Haiku."""
    if not msg:
        return False
    norm = _normalize(msg)
    return any(kw in norm for kw in _MEDIUM_CONFIDENCE_HUMAN)


def _looks_like_stock_query(msg: str) -> bool:
    """
    Heurística rápida para detectar búsquedas de vehículos sin llamar a Haiku.
    Se dispara cuando el usuario claramente quiere ver carros/camionetas/modelos.
    """
    text = (msg or "").lower()
    if not text:
        return False

    # Si el mensaje tiene contexto de participación, NO es búsqueda de stock
    palabras_participacion = (
        "participar", "participando", "quiero participar", "voy a participar",
        "vi en", "lo vi", "que vi", "encontré", "encontre", "está en la web",
        "esta en la web", "lo tengo", "ya lo vi", "ese carro", "ese auto",
        "ese vehículo", "ese vehiculo", "en esa subasta", "ya encontré",
        "me interesa ese", "me interesa esa", "voy a pujar", "quiero pujar",
        "quiero ofertar", "voy a ofertar", "ya lo encontré", "ya lo encontre",
        "lo encontré en", "lo encontre en", "está listado", "esta listado",
        "aparece en", "lo vi en", "la vi en", "que aparece", "que está en",
        "que esta en", "quiero hacer una oferta", "quiero hacer oferta",
    )
    if any(p in text for p in palabras_participacion):
        return False

    verbos_stock = (
        "tienen", "tiene", "hay", "busco", "buscando", "quiero ver",
        "mostrar", "muestrame", "muéstrame", "que carros hay", "qué carros hay",
        "listar", "lista", "stock", "disponible", "disponibles",
    )
    palabras_vehiculo = (
        "carro", "carros", "auto", "autos", "vehiculo", "vehículo", "camioneta",
        "camionetas", "suv", "pickup", "4x4", "kia", "hyundai", "toyota",
        "chevrolet", "nissan", "honda", "mazda", "bmw", "mercedes", "audi",
        "picanto", "sportage", "tucson", "hilux", "yaris", "corolla",
    )
    hay_verbo = any(v in text for v in verbos_stock)
    hay_vehiculo = any(w in text for w in palabras_vehiculo)
    return hay_verbo and hay_vehiculo


def _looks_like_human_support(msg: str) -> bool:
    """
    Heurística rápida para detectar señales EXPLÍCITAS de escalación al soporte humano.
    Cubre EXP-01 a EXP-15 del documento de arquitectura de escalación.
    Se dispara antes de llamar a Haiku, igual que _looks_like_stock_query.
    """
    text = (msg or "").lower()
    if not text:
        return False

    # EXP-01/02: Petición directa estándar y coloquial
    frases_directas = (
        "quiero un asesor", "necesito un asesor", "hablar con un asesor",
        "me ayuda un asesor", "quiero hablar con alguien", "hablar con alguien",
        "hablar con una persona", "hablar con un humano", "quiero un agente",
        "necesito un agente", "hablar con el agente", "me comunica con un agente",
        "quiero hablar con un ejecutivo", "quiero un ejecutivo",
        "atención al cliente", "quiero soporte humano", "soporte humano",
        "me atiende una persona", "me atiende un humano", "quiero ayuda humana",
        "me comunicas con", "comunícame con", "comunicame con",
        "transferirme con", "transferir con alguien",
        "alguien real", "al toque con un", "pásame con", "pasame con",
    )
    if any(f in text for f in frases_directas):
        return True

    # EXP-03: Rechazo frontal al bot
    rechazo_bot = (
        "no quiero hablar con una máquina", "no quiero hablar con una maquina",
        "no quiero hablar con el bot", "deja de responder", "no me sirve este bot",
        "este bot no sirve", "no quiero un bot", "atiéndeme una persona",
        "atiendeme una persona",
    )
    if any(f in text for f in rechazo_bot):
        return True

    # EXP-04: Transición de canal (quiere llamar)
    canal_llamada = (
        "a qué número llamo", "a que numero llamo",
        "dame un teléfono", "dame un telefono",
        "dame el número", "dame el numero",
        "quiero llamar a un asesor", "quiero llamar al equipo",
        "número para llamar", "numero para llamar",
    )
    if any(f in text for f in canal_llamada):
        return True

    # EXP-05/06: Frustración abierta leve y severa
    frustracion = (
        "este bot no entiende", "tu bot es una vaina", "qué vaina de bot", "que vaina de bot",
        "ya me tienen harto", "me tienen harto", "estoy asado", "estoy harto",
        "pésimo servicio", "pesimo servicio", "horrible servicio",
    )
    if any(f in text for f in frustracion):
        return True

    # EXP-07: Hostilidad / lisuras
    hostilidad = ("bot de m", "porquería de servicio", "porqueria de servicio", "servicio inservible",)
    if any(f in text for f in hostilidad):
        return True

    # EXP-09: Reclamo de consignación
    reclamo_consignacion = (
        "perdí mi consignación", "perdi mi consignacion",
        "no aparece mi garantía", "no aparece mi garantia",
        "perdí mi garantía", "perdi mi garantia",
    )
    if any(f in text for f in reclamo_consignacion):
        return True

    # EXP-11: Acusación de fraude / estafa
    fraude = ("estafadores", "son estafadores", "es una estafa", "están robando", "estan robando",)
    if any(f in text for f in fraude):
        return True

    # EXP-12: Recurso legal Indecopi
    legal = ("indecopi", "voy a denunciar", "quiero quejarme formalmente", "denuncia formal",)
    if any(f in text for f in legal):
        return True

    # EXP-13: Libro de reclamaciones
    if any(f in text for f in ("libro de reclamaciones", "libro de reclamos")):
        return True

    # EXP-15: Devolución / abandono con reclamo de fondos
    devolucion = ("devuélveme mi plata", "devuelveme mi plata", "quiero mi plata de vuelta",)
    if any(f in text for f in devolucion):
        return True

    # Combinación verbo de solicitud + sustantivo de persona
    verbos_solicitud = ("quiero", "necesito", "dame", "me das", "me comunica", "pasame", "pásame", "llamar a")
    sustantivos_persona = ("asesor", "asesora", "agente", "ejecutivo", "ejecutiva", "humano", "persona", "representante")
    hay_verbo = any(v in text for v in verbos_solicitud)
    hay_sustantivo = any(s in text for s in sustantivos_persona)
    return hay_verbo and hay_sustantivo


def classify_escalation_type(msg: str) -> str:
    """
    Determina el subtipo de escalación para elegir el mensaje de espera correcto.
    Returns uno de:
      'legal_threat'    → Indecopi, fraude, libro de reclamaciones
      'live_auction'    → urgencia durante subasta en vivo
      'legal_vehicle'   → Sunarp, embargos, gravámenes, órdenes de captura
      'frustrated'      → frustración severa, hostilidad
      'pre_abandonment' → quiere abandonar el proceso por fricción
      'confusion'       → no entiende el proceso tras varios intentos
      'b2b'             → potencial cliente corporativo / flota
      'doubt'           → duda que el bot no puede resolver (no pidió asesor explícitamente)
      'direct_simple'   → pidió asesor directamente sin mayor contexto
    """
    text = (msg or "").lower()

    # Amenaza legal / regulatoria
    if any(s in text for s in ("indecopi", "libro de reclamaciones", "libro de reclamos", "estafadores", "estafa", "fraude", "denunciar", "quejarme formalmente")):
        return "legal_threat"

    # Urgencia en subasta en vivo
    if any(s in text for s in ("en vivo", "acaba en", "queda 1 minuto", "queda un minuto", "botón no", "boton no", "no me hace click", "se congeló", "se congelo", "me botó", "me boto", "subasta está")):
        return "live_auction"

    # Consulta legal de vehículo (Sunarp, embargos, gravámenes)
    if any(s in text for s in ("sunarp", "embargo", "gravamen", "gravámenes", "orden de captura", "papeleta", "prenda vehicular", "saneamiento")):
        return "legal_vehicle"

    # Frustración severa / hostilidad
    if any(s in text for s in ("harto", "asado", "vaina", "porquería", "porqueria", "inservible", "pésimo", "pesimo", "horrible servicio", "bot de m")):
        return "frustrated"

    # Pre-abandono (quiere irse por fricción)
    if any(s in text for s in ("mucho trámite", "mucho tramite", "lo dejo ahí", "lo dejo ahi", "ya no quiero", "facebook", "demasiado", "muy complicado", "mejor busco", "lo voy a pensar", "ya me desanimé", "ya me desanime", "mucho proceso", "pura burocracia")):
        return "pre_abandonment"

    # Confusión repetida (no capta el proceso)
    if any(s in text for s in ("no entiendo", "no capto", "no comprendo", "explícame", "explicame", "a ver dime", "cómo así", "como asi", "no me queda claro", "no me queda")):
        return "confusion"

    # Potencial B2B / flota empresarial
    if any(s in text for s in ("flota", "flotilla", "varios carros", "varios autos", "mi empresa quiere", "liquidar la flota", "liquidar los carros", "camión volvo", "camion volvo", "tasación de flota", "tasacion de flota")):
        return "b2b"

    # Duda que el bot no pudo resolver (no pidió asesor explícitamente, pero tiene pregunta)
    pidio_asesor_explicitamente = any(s in text for s in ("asesor", "agente", "ejecutivo", "humano", "persona", "alguien", "número", "numero"))
    tiene_pregunta = "?" in text or any(s in text for s in ("cómo", "como", "qué es", "qué pasa", "cuándo", "cuando", "cuánto", "cuanto", "dónde", "donde"))
    if not pidio_asesor_explicitamente and tiene_pregunta:
        return "doubt"

    return "direct_simple"


def _usage_to_dict(usage) -> dict:
    """Extrae input/output/cache del objeto usage de Anthropic."""
    if usage is None:
        return {"input": 0, "output": 0, "cached_read": 0, "cached_creation": 0}
    return {
        "input": getattr(usage, "input_tokens", 0) or 0,
        "output": getattr(usage, "output_tokens", 0) or 0,
        "cached_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cached_creation": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


INTENT_EXPLANATIONS = {
    "faq": "Pregunta sobre la plataforma (comisiones, registro, SubasCoins, etc.). Se responde con RAG.",
    "stock_search": "El usuario quiere buscar o listar vehículos. Por ahora mostramos mensaje de próximamente.",
    "soporte_humano": "Pide hablar con un agente o humano. Se responde con contacto.",
    "fuera_dominio": "Tema fuera de VMC Subastas. Se responde con redirección al dominio.",
}


def get_top_k_for_intent(intent: str, user_message: str) -> int:
    """
    TOP_K dinámico para la búsqueda en Pinecone según intent y longitud del mensaje.
    - faq + mensaje < 8 palabras (intención clara) → 3
    - faq + mensaje >= 8 palabras (compuesta/ambigua) → 5
    - stock_search → 3 (el inventario no está en Pinecone)
    - resto → 3
    """
    msg = (user_message or "").strip()
    words = len(msg.split()) if msg else 0
    if intent == "stock_search":
        return 3
    if intent == "faq":
        return 5 if words >= 8 else 3
    return 3


def classify_intent(user_message: str, last_assistant_message: str | None = None) -> str:
    """
    Clasifica la intención del usuario. Devuelve uno de: faq, stock_search, soporte_humano, fuera_dominio.
    Si last_assistant_message está informado, el router considera que el usuario puede estar respondiendo
    a una pregunta del bot (ej. "Si" como respuesta a "¿Ya tienes estos datos?") y clasifica como faq.
    """
    out, _, _, _ = classify_intent_with_debug(user_message, last_assistant_message=last_assistant_message)
    return out


def classify_intent_with_debug(
    user_message: str,
    last_assistant_message: str | None = None,
) -> tuple[str, int, dict, str]:
    """
    Igual que classify_intent pero retorna (intent, latency_ms, tokens_dict, explanation).
    Si last_assistant_message está informado, se incluye en el prompt para no clasificar
    respuestas cortas (sí, no, ok, dale) como fuera_dominio cuando el usuario responde al bot.
    """
    import time
    msg = (user_message or "").strip()
    if not msg:
        return "faq", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["faq"]

    # Capa 1 — heurística de alta confianza: soporte_humano sin costo
    if _looks_like_human_support(msg):
        return "soporte_humano", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["soporte_humano"]

    # Capa 2 — heurística de alta confianza: stock_search sin costo
    if _looks_like_stock_query(msg):
        return "stock_search", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["stock_search"]

    # Heurística local: si el usuario pide hablar con un asesor/agente humano.
    if _looks_like_human_support(msg):
        return "soporte_humano", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["soporte_humano"]

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "faq", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["faq"]

    # Capa 3 — flag de media confianza para orientar a Haiku
    posible_frustracion = _might_need_human(msg)
    frustration_flag = (
        "\n<system_flag>POSIBLE_FRUSTRACION_DETECTADA: el mensaje contiene señales de "
        "impaciencia o molestia. Evalúa con especial atención si el usuario realmente "
        "está pidiendo un agente humano o solo está expresando urgencia.</system_flag>"
        if posible_frustracion else ""
    )

    # Contexto de último mensaje del asistente
    context_block = ""
    if last_assistant_message and (last_assistant_message or "").strip():
        context_block = (
            "\n<last_assistant_message>"
            + (last_assistant_message or "").strip()[:500]
            + "</last_assistant_message>"
            "\nSi el usuario está respondiendo directamente a esa pregunta del asistente "
            "(respuesta corta como 'sí', 'no', 'ok', 'dale'), clasifica como faq."
        )

    prompt = f"""<role>
Eres el clasificador de intención del chatbot de VMC Subastas, plataforma de subastas de vehículos usados en Perú. Tu única tarea es clasificar el mensaje del usuario en exactamente UNA categoría.
</role>

<intents_definition>
- faq: Preguntas sobre la plataforma (registro, cuenta, SubasCoins, billetera, consignación, ofertas En Vivo o Negociable, visitas, comisiones, proceso de compra, plazos). También saludos simples y respuestas cortas a preguntas del asistente.

- stock_search: El usuario quiere buscar o listar vehículos que aún no conoce ("¿tienen una Hilux?", "qué carros hay"). NO aplica si ya identificó el vehículo y habla de participar u ofertar.

- soporte_humano: El usuario pide hablar con una persona real, agente, ejecutivo o asesor. También aplica cuando: está molesto y escala emocionalmente, usa jerga peruana de queja (asado, palta, floro, lenteja), amenaza con Indecopi o libro de reclamaciones, expresa abandono ("da igual", "olvídalo", "ya no quiero"), usa sarcasmo ("qué gran ayuda"), o repite la misma consulta varias veces sin ser atendido.

- fuera_dominio: El mensaje claramente no tiene relación con VMC Subastas ni vehículos ni subastas. Nunca uses fuera_dominio para saludos o mensajes ambiguos cortos — ante la duda clasifica como faq.
</intents_definition>

<instructions>
1. Lee el mensaje del usuario con atención completa al tono emocional, no solo al contenido literal.
2. Detecta jerga peruana de queja o frustración: "asado/a", "qué palta", "qué yuca", "puro floro", "me está floreando", "qué lenteja", "al toque", "oe".
3. Detecta señales de abandono transaccional: "da igual", "olvídalo", "ya fue", "ya no quiero" → siempre soporte_humano para rescate comercial.
4. Detecta escalada legal: "Indecopi", "libro de reclamaciones", "quiero reclamar" → siempre soporte_humano.
5. Usa <thinking> para razonar brevemente antes de decidir.
6. Responde SOLO con la etiqueta <intent> conteniendo exactamente una palabra: faq, stock_search, soporte_humano o fuera_dominio.
</instructions>

<examples>
  <example>
    <user_input>quiero ver autos toyota yaris del 2020</user_input>
    <thinking>El usuario quiere listar vehículos que no conoce aún. No hay frustración.</thinking>
    <intent>stock_search</intent>
  </example>
  <example>
    <user_input>oe tu bot es una lenteja, la subasta ya va a cerrar pásame con alguien al toque</user_input>
    <thinking>Usa "lenteja" (peruanismo de queja), urgencia extrema ("al toque") y pide explícitamente hablar con alguien.</thinking>
    <intent>soporte_humano</intent>
  </example>
  <example>
    <user_input>ya van 3 veces q intento pujar y sale error de sesion, q palta de sistema</user_input>
    <thinking>"q palta" es peruanismo de frustración. Reporta un fallo técnico repetitivo que bloquea al usuario en su cuenta. Necesita agente.</thinking>
    <intent>soporte_humano</intent>
  </example>
  <example>
    <user_input>ok, da igual, ya no quiero comprar nada olvídalo</user_input>
    <thinking>Abandono transaccional claro. "da igual" y "olvídalo" indican resignación. Escalada para rescate comercial.</thinking>
    <intent>soporte_humano</intent>
  </example>
  <example>
    <user_input>qué gran ayuda me diste eh... me voy a indecopi</user_input>
    <thinking>Sarcasmo ("qué gran ayuda") + amenaza legal (Indecopi). Escalada inmediata.</thinking>
    <intent>soporte_humano</intent>
  </example>
  <example>
    <user_input>me tas floreando, no veo mi deposito en la plataforma</user_input>
    <thinking>"floreando" es peruanismo que acusa evasión. Problema financiero específico de su cuenta, no FAQ genérico.</thinking>
    <intent>soporte_humano</intent>
  </example>
  <example>
    <user_input>cómo funciona el sistema de garantía</user_input>
    <thinking>Pregunta teórica general sobre la plataforma. Sin frustración ni urgencia específica de cuenta.</thinking>
    <intent>faq</intent>
  </example>
</examples>
{frustration_flag}{context_block}

<input>{msg[:300]}</input>"""

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        t0 = time.perf_counter()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
            }],
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = getattr(response, "usage", None)
        tokens = _usage_to_dict(usage)
        block = response.content[0] if response.content else None
        raw = (block.text if block and hasattr(block, "text") else str(response.content)).strip()
        # Extraer el intent de la etiqueta <intent>...</intent>
        match = re.search(r"<intent>\s*(\w+)\s*</intent>", raw, re.IGNORECASE)
        text = match.group(1).lower() if match else raw.lower()
        for intent in INTENTS:
            if intent in text or text == intent:
                return intent, latency_ms, tokens, INTENT_EXPLANATIONS.get(intent, "")
        return "faq", latency_ms, tokens, INTENT_EXPLANATIONS["faq"]
    except Exception:
        return "faq", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["faq"]
