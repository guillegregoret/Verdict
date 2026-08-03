"""Cliente del command server de IBC (para /reconnect).

IBC (el controller que corre adentro del IB Gateway) expone un command server TCP
que acepta comandos de texto: RESTART, STOP, RELOGIN, ENABLEAPI… Enviarle RESTART
hace que el gateway se re-loguee contra IBKR, y ESO dispara el push de 2FA al
celular del usuario.

🔴 READ-ONLY respecto del broker: solo le pide al gateway que se reconecte. No
envía órdenes ni toca el Docker socket — la app mantiene su blindaje (§8/§12). El
gateway sólo escucha en la red interna de Docker, nunca expuesto.
"""

from __future__ import annotations

import socket
from typing import Protocol

from ..config import Settings
from ..logging import get_logger

logger = get_logger(__name__)


class GatewayControlError(RuntimeError):
    """Error hablando con el command server de IBC."""


class SocketLike(Protocol):
    def sendall(self, data: bytes) -> None: ...
    def recv(self, bufsize: int) -> bytes: ...
    def close(self) -> None: ...


def _default_connect(host: str, port: int, timeout: float) -> SocketLike:
    return socket.create_connection((host, port), timeout=timeout)


class GatewayControl:
    """Manda comandos al command server de IBC del gateway (TCP de texto)."""

    def __init__(self, settings: Settings, connect=_default_connect) -> None:
        self._settings = settings
        self._connect = connect

    def restart(self) -> str:
        """Pide al gateway re-loguearse (dispara el 2FA). Devuelve la respuesta de IBC.

        Lanza GatewayControlError si el command server está deshabilitado o no
        responde — el caller lo traduce a un mensaje para el usuario.
        """
        return self._send("RESTART")

    def _send(self, command: str) -> str:
        s = self._settings
        if not s.ib_gateway_command_enabled:
            raise GatewayControlError(
                "El command server de IBC no está habilitado "
                "(IB_GATEWAY_COMMAND_ENABLED=false)."
            )
        try:
            conn = self._connect(
                s.ib_gateway_command_host,
                s.ib_gateway_command_port,
                s.ib_gateway_timeout_seconds,
            )
        except OSError as exc:
            raise GatewayControlError(
                f"No se pudo conectar al command server del gateway "
                f"({s.ib_gateway_command_host}:{s.ib_gateway_command_port}): {exc}"
            ) from exc
        try:
            conn.sendall(f"{command}\n".encode())
            raw = conn.recv(2048)
        except OSError as exc:
            raise GatewayControlError(f"Fallo enviando {command} al gateway: {exc}") from exc
        finally:
            conn.close()
        resp = raw.decode("utf-8", "replace").strip()
        logger.info("IBC command %s → %r", command, resp)
        return resp or f"{command} enviado (sin respuesta)"
