"""
src/core/resilience.py
----------------------
Manejo de errores y reintentos para llamadas a la API de Anthropic.

Resuelve:
  - Error 429 (rate limit) que hoy rompe la experiencia del usuario
  - Falta de reintentos automáticos ante errores temporales
  - Mensajes de error técnicos expuestos al usuario

Estrategia: retry con backoff exponencial
  - Intento 1 falla → espera 1s → reintenta
  - Intento 2 falla → espera 2s → reintenta
  - Intento 3 falla → espera 4s → reintenta
  - Si los 3 fallan → mensaje amigable al usuario
"""

import time
from anthropic import RateLimitError, APIStatusError, APIConnectionError

from src.core.logger import log_error, log_event

# ---------------------------------------------------------------------------
# 1. Mensaje amigable para el usuario
#    Nunca se expone el error técnico crudo. Siempre este mensaje.
# ---------------------------------------------------------------------------
MENSAJE_OCUPADO = (
    "En este momento estoy recibiendo muchas consultas. "
    "Por favor intenta nuevamente en unos segundos. 🙏"
)

# ---------------------------------------------------------------------------
# 2. Excepciones propias
#    - UserFacingError: se muestra al usuario con MENSAJE_OCUPADO.
#    - FatalAPIError: errores fatales de Anthropic donde NO tiene sentido reintentar
#      (por ejemplo, saldo insuficiente).
# ---------------------------------------------------------------------------
class UserFacingError(Exception):
    """
    Error que debe mostrarse al usuario con un mensaje amigable.
    No expone detalles técnicos.
    """
    def __init__(self, mensaje: str = MENSAJE_OCUPADO):
        self.mensaje = mensaje
        super().__init__(mensaje)


class FatalAPIError(Exception):
    """
    Error fatal al llamar a la API de Anthropic.
    No es recuperable mediante reintentos (ej: saldo insuficiente).
    """
    pass


# ---------------------------------------------------------------------------
# 3. Helpers internos
# ---------------------------------------------------------------------------
def _system_has_cache_control(system) -> bool:
    """
    Devuelve True si el system prompt es una lista de bloques con cache_control.
    """
    if not isinstance(system, list):
        return False
    return any(
        isinstance(block, dict) and "cache_control" in block
        for block in system
    )


# ---------------------------------------------------------------------------
# 4. Función principal: call_claude_with_retry()
# ---------------------------------------------------------------------------
def call_claude_with_retry(client, max_retries: int = 3, **kwargs):
    """
    Llama a client.messages.create() con reintentos automáticos.

    En Anthropic >= 0.34.0, Prompt Caching es GA y funciona directo
    en messages.create() sin necesidad del endpoint beta.

    Args:
        client:       Cliente de Anthropic ya inicializado.
        max_retries:  Número máximo de intentos (default: 3).
        **kwargs:     Parámetros que se pasan directo a messages.create()

    Returns:
        Respuesta de Anthropic si algún intento tiene éxito.

    Raises:
        UserFacingError: Si todos los reintentos fallan.
        FatalAPIError:   Si el saldo es insuficiente (no reintenta).
    """
    use_beta_caching = _system_has_cache_control(kwargs.get("system"))

    for attempt in range(1, max_retries + 1):
        try:
            log_event(
                "anthropic_llamada",
                attempt=attempt,
                model=kwargs.get("model", "desconocido"),
                caching=use_beta_caching,
            )

            # En Anthropic >= 0.34.0, Prompt Caching es GA y funciona directo en messages.create()
            respuesta = client.messages.create(**kwargs)

            # Éxito — registramos y devolvemos
            log_event(
                "anthropic_exito",
                attempt=attempt,
                model=kwargs.get("model", "desconocido"),
            )
            return respuesta

        except RateLimitError as e:
            # Error 429 — demasiadas solicitudes
            wait = 2 ** (attempt - 1)  # 1s, 2s, 4s
            log_error(
                "anthropic_429",
                message=str(e),
                attempt=attempt,
                wait_seconds=wait,
                model=kwargs.get("model", "desconocido"),
            )
            if attempt == max_retries:
                raise UserFacingError()
            time.sleep(wait)

        except APIConnectionError as e:
            # Sin conexión a internet o problema de red
            wait = 2 ** (attempt - 1)
            log_error(
                "anthropic_conexion",
                message=str(e),
                attempt=attempt,
                wait_seconds=wait,
            )
            if attempt == max_retries:
                raise UserFacingError(
                    "No puedo conectarme en este momento. "
                    "Verifica tu conexión e intenta nuevamente."
                )
            time.sleep(wait)

        except APIStatusError as e:
            # Otros errores de Anthropic (400, 500, 503, etc.)
            msg = str(e)
            low = msg.lower()

            # Caso FATAL: saldo insuficiente → no tiene sentido reintentar
            if "credit balance is too low" in low:
                log_error(
                    "fatal_api_error",
                    message=msg,
                    status_code=getattr(e, "status_code", None),
                    attempt=attempt,
                    model=kwargs.get("model", "desconocido"),
                )
                raise FatalAPIError(msg)

            # Resto de errores de estado: backoff ligero y reintento
            log_error(
                "anthropic_status",
                message=msg,
                status_code=e.status_code,
                attempt=attempt,
            )
            if attempt == max_retries:
                raise UserFacingError()
            time.sleep(2)


# ---------------------------------------------------------------------------
# 5. Constante exportable con el mensaje amigable
# ---------------------------------------------------------------------------
__all__ = [
    "call_claude_with_retry",
    "UserFacingError",
    "FatalAPIError",
    "MENSAJE_OCUPADO",
]
