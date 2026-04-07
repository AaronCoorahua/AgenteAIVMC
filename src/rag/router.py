"""
Router de intención: clasifica el mensaje del usuario con Claude Haiku.
Devuelve: faq | stock_search | soporte_humano | fuera_dominio
Sin LangChain; llamada directa a Anthropic API.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

INTENTS = ("faq", "stock_search", "soporte_humano", "fuera_dominio")


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

    # Heurística local: si el mensaje claramente es una búsqueda de vehículos,
    # marcamos stock_search sin llamar a Haiku (más barato y más robusto).
    if _looks_like_stock_query(msg):
        return "stock_search", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["stock_search"]

    # Heurística local: si el usuario pide hablar con un asesor/agente humano.
    if _looks_like_human_support(msg):
        return "soporte_humano", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["soporte_humano"]

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "faq", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["faq"]

    prompt = """Eres un clasificador de intención para el chatbot de VMC Subastas (plataforma de subastas de vehículos en Perú). Clasifica el mensaje del usuario en exactamente UNA de estas categorías:

- faq: preguntas sobre registro, cuenta, SubasCoins, billetera, consignación, ofertas En Vivo o Negociable, visitas, comisiones, proceso de compra, plazos, soporte. Cualquier duda sobre cómo funciona la plataforma. También saludos simples (hola, buenas, qué tal, hey, buenos días) y respuestas cortas (sí, no, ok, claro, dale) a una pregunta que el asistente acaba de hacer — en esos casos es faq para que la conversación continúe.

- stock_search: el usuario quiere buscar, ver o listar vehículos disponibles que aún no conoce (ej. "¿tienen una Hilux?", "qué carros hay", "busco una camioneta 4x4"). NO es stock_search si el usuario ya identificó un vehículo específico y está hablando de participar en él, hacer una oferta, o preguntar sobre ese proceso.

- soporte_humano: pide hablar con una persona, agente, ejecutivo, o está molesto y quiere escalar (ej. "quiero hablar con alguien", "me atiende un humano", "no me sirve esto").

- fuera_dominio: el mensaje claramente no tiene relación con VMC Subastas ni con vehículos ni con subastas — por ejemplo preguntas sobre política, clima, recetas, chistes, temas completamente ajenos. Nunca uses fuera_dominio para saludos o mensajes ambiguos cortos — ante la duda clasifica como faq.

Responde SOLO con una palabra: faq, stock_search, soporte_humano o fuera_dominio. Nada más."""

    if last_assistant_message and (last_assistant_message or "").strip():
        prompt += '\nContexto: El último mensaje del asistente fue: "' + (last_assistant_message or "").strip()[:500] + '". El usuario ahora dice: "' + msg[:200] + '". Si el usuario está respondiendo a esa pregunta del asistente (respuesta corta), clasifica como faq.\n\nMensaje del usuario a clasificar: ' + msg
    else:
        prompt += "\nMensaje del usuario: " + msg

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        t0 = time.perf_counter()
        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
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
        text = (block.text if block and hasattr(block, "text") else str(response.content)).strip().lower()
        for intent in INTENTS:
            if intent in text or text == intent:
                return intent, latency_ms, tokens, INTENT_EXPLANATIONS.get(intent, "")
        return "faq", latency_ms, tokens, INTENT_EXPLANATIONS["faq"]
    except Exception:
        return "faq", 0, _usage_to_dict(None), INTENT_EXPLANATIONS["faq"]
