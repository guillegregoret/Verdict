"""Tests del escalado de umbrales por volatilidad (beta)."""

from __future__ import annotations

from portfolio_monitor.thresholds import scaled_thresholds


def test_beta_one_keeps_base() -> None:
    assert scaled_thresholds(1.0, -2.7, 4.0) == (-2.7, 4.0)


def test_low_beta_tightens_but_clamped() -> None:
    # ABBV beta 0.27 → factor pisado a 0.5 → umbrales más chicos (más sensible).
    assert scaled_thresholds(0.27, -2.7, 4.0) == (-1.35, 2.0)


def test_high_beta_widens_but_clamped() -> None:
    # beta 3.0 → factor topeado a 2.0 → umbrales más grandes (menos sensible).
    assert scaled_thresholds(3.0, -2.7, 4.0) == (-5.4, 8.0)


def test_moderate_beta_scales_proportionally() -> None:
    drop, rise = scaled_thresholds(1.5, -2.7, 4.0)
    assert drop == -4.05
    assert rise == 6.0


def test_missing_or_invalid_beta_returns_base() -> None:
    assert scaled_thresholds(None, -2.7, 4.0) == (-2.7, 4.0)
    assert scaled_thresholds(0.0, -2.7, 4.0) == (-2.7, 4.0)
    assert scaled_thresholds(-1.0, -2.7, 4.0) == (-2.7, 4.0)
