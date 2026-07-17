"""Tests del módulo de razonamiento con fakes (sin SDK de Anthropic ni red)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import FundamentalsRow
from portfolio_monitor.reasoning import (
    AnthropicReasoner,
    ReasoningContext,
    ReasoningError,
    ReasoningService,
    Suggestion,
    TemplateReasoner,
)
from portfolio_monitor.reasoning.reasoners import Reasoner
from portfolio_monitor.trigger import TriggerEvent


def _fundamentals() -> FundamentalsRow:
    return FundamentalsRow(
        ticker="NVDA",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        pe=30.5,
        revenue_growth=0.22,
        gross_margin=0.75,
        debt_to_equity=0.4,
    )


def _context(
    fundamentals: FundamentalsRow | None = None,
    bucket: float | None = None,
    *,
    pct_change: float = -5.2,
    verdict: str = "Mantener",
    action: str = "comprar_dip",
):
    return ReasoningContext(
        ticker="NVDA",
        pct_change=pct_change,
        window_minutes=390,
        verdict=verdict,
        current_price=95.0,
        reference_price=100.0,
        action=action,
        fundamentals=fundamentals,
        bucket_remaining=bucket,
    )


# ── ReasoningContext.from_trigger_event ──────────────────────────────────────
def test_context_from_trigger_event() -> None:
    event = TriggerEvent(
        ticker="NVDA",
        pct_change=-5.2,
        window_minutes=390,
        reference_price=100.0,
        current_price=95.0,
        verdict="Mantener",
    )
    ctx = ReasoningContext.from_trigger_event(event, fundamentals=_fundamentals())
    assert ctx.ticker == "NVDA"
    assert ctx.pct_change == -5.2
    assert ctx.verdict == "Mantener"
    assert ctx.fundamentals is not None and ctx.fundamentals.pe == 30.5


# ── TemplateReasoner ─────────────────────────────────────────────────────────
def test_template_includes_key_fields() -> None:
    s = TemplateReasoner().generate(_context(_fundamentals(), bucket=500.0))
    assert s.source == "template"
    assert "NVDA" in s.text
    assert "Mantener" in s.text
    assert "P/E 30.5" in s.text
    assert "Cash disponible: $500" in s.text


def test_template_handles_missing_fundamentals() -> None:
    s = TemplateReasoner().generate(_context(fundamentals=None))
    assert "no disponibles" in s.text


def test_template_includes_dca_suggestion() -> None:
    ctx = ReasoningContext(
        ticker="NVDA", verdict="Mantener", pct_change=-3.0, window_minutes=390,
        current_price=97.0, reference_price=100.0, action="comprar_dip",
        dca_suggested_usd=130.0, bucket_remaining=500.0,
    )
    s = TemplateReasoner().generate(ctx)
    assert "DCA sugerido" in s.text
    assert "130" in s.text


def test_template_frames_take_profit_on_rise() -> None:
    # una suba con acción tomar_ganancias → flecha ↑ y etiqueta de la acción
    s = TemplateReasoner().generate(
        _context(pct_change=6.0, verdict="Trim - tomar ganancias", action="tomar_ganancias")
    )
    assert "📈" in s.text
    assert "TOMAR GANANCIAS" in s.text


def test_template_frames_fundamentals_decay() -> None:
    ctx = ReasoningContext(
        ticker="NVDA", verdict="Mantener", signal_kind="fundamentals_decay",
        action="revisar_tesis", note="margen bruto 75.0% → 68.0%",
    )
    s = TemplateReasoner().generate(ctx)
    assert "⚠️" in s.text
    assert "deteriorados" in s.text
    assert "margen bruto" in s.text


def test_context_carries_action_from_event() -> None:
    event = TriggerEvent(
        ticker="MU",
        pct_change=6.1,
        window_minutes=390,
        reference_price=100.0,
        current_price=106.1,
        verdict="Trim - tomar ganancias",
        trigger_type="rise_pct",
        action="tomar_ganancias",
    )
    ctx = ReasoningContext.from_trigger_event(event)
    assert ctx.action == "tomar_ganancias"


# ── AnthropicReasoner con client fake ────────────────────────────────────────
class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, blocks: list[_Block], stop_reason: str = "end_turn") -> None:
        self.content = blocks
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp
        self.last_kwargs: dict | None = None

    def create(self, **kwargs: object) -> _Resp:
        self.last_kwargs = dict(kwargs)
        return self._resp


class _FakeClient:
    def __init__(self, resp: _Resp) -> None:
        self.messages = _FakeMessages(resp)


def _settings() -> Settings:
    return Settings(_env_file=None, anthropic_model="claude-opus-4-8")


def test_anthropic_reasoner_returns_text_and_builds_prompt() -> None:
    client = _FakeClient(_Resp([_Block("Podés sumar ~$300 en NVDA.")]))
    reasoner = AnthropicReasoner(_settings(), client=client)

    suggestion = reasoner.generate(_context(_fundamentals()))

    assert suggestion == Suggestion(text="Podés sumar ~$300 en NVDA.", source="anthropic")
    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert "NVDA" in kwargs["messages"][0]["content"]
    assert kwargs["system"]  # hay system prompt


def test_anthropic_reasoner_raises_on_refusal() -> None:
    client = _FakeClient(_Resp([], stop_reason="refusal"))
    with pytest.raises(ReasoningError):
        AnthropicReasoner(_settings(), client=client).generate(_context())


def test_anthropic_reasoner_raises_on_empty_text() -> None:
    client = _FakeClient(_Resp([_Block("   ")]))
    with pytest.raises(ReasoningError):
        AnthropicReasoner(_settings(), client=client).generate(_context())


def test_anthropic_reasoner_missing_key_raises() -> None:
    with pytest.raises(ReasoningError):
        AnthropicReasoner(Settings(_env_file=None, anthropic_api_key=""))


# ── ReasoningService (fallback) ──────────────────────────────────────────────
class _BoomReasoner:
    def generate(self, context: ReasoningContext) -> Suggestion:
        raise ReasoningError("boom")


class _OkReasoner:
    def __init__(self, source: str) -> None:
        self._source = source

    def generate(self, context: ReasoningContext) -> Suggestion:
        return Suggestion(text=f"ok-{self._source}", source=self._source)


def test_service_uses_primary_when_ok() -> None:
    svc = ReasoningService(primary=_OkReasoner("anthropic"), fallback=_OkReasoner("template"))
    assert svc.suggest(_context()).source == "anthropic"


def test_service_falls_back_on_error() -> None:
    svc = ReasoningService(primary=_BoomReasoner(), fallback=_OkReasoner("template"))
    result = svc.suggest(_context())
    assert result.source == "template"


def test_service_reraises_without_fallback() -> None:
    svc = ReasoningService(primary=_BoomReasoner())
    with pytest.raises(ReasoningError):
        svc.suggest(_context())


def _reasoner_type_check(r: Reasoner) -> Reasoner:  # documenta que cumplen el protocolo
    return r


def test_reasoners_satisfy_protocol() -> None:
    _reasoner_type_check(TemplateReasoner())
