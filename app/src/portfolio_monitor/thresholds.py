"""Escala de umbrales de trigger por volatilidad (beta).

Un umbral uniforme (todos -2.7% / +4.0%) spamea los nombres volátiles y silencia
los tranquilos: NVDA y ABBV no deberían gatillar con el mismo movimiento. Se
escala el umbral por el beta del activo, acotado a una banda para no exagerar.

Función pura: el seeding de `ticker_config` la usa una vez (los umbrales quedan
como config editable por el usuario); beta se mueve lento, no hace falta recalcular
en cada ciclo.
"""

from __future__ import annotations

# Banda del factor de escala: por debajo/encima no se estira más (evita umbrales
# absurdos con betas extremos o mal reportados).
_BETA_FLOOR = 0.5
_BETA_CAP = 2.0


def scaled_thresholds(
    beta: float | None, base_drop: float, base_rise: float
) -> tuple[float, float]:
    """(umbral_caída, umbral_suba) escalados por beta respecto de un beta 1.

    `base_drop` es negativo (ej: -2.7), `base_rise` positivo (ej: +4.0). Con beta
    None o no positivo se devuelven los base sin tocar. El factor se acota a
    [0.5, 2.0]: ABBV (beta 0.27) → 0.5×; NVDA (beta 1.7) → 1.7×.
    """
    if beta is None or beta <= 0:
        return base_drop, base_rise
    factor = min(max(beta, _BETA_FLOOR), _BETA_CAP)
    return round(base_drop * factor, 2), round(base_rise * factor, 2)
