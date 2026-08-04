"""Tests del módulo de razonamiento con fakes (sin SDK de Anthropic ni red)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import FundamentalsRow
from portfolio_monitor.market import BenchmarkMove, MarketSnapshot
from portfolio_monitor.reasoning import (
    AnthropicReasoner,
    ClusterExposure,
    PortfolioReviewContext,
    ReasoningContext,
    ReasoningError,
    ReasoningService,
    Suggestion,
    TemplateReasoner,
)
from portfolio_monitor.reasoning.reasoners import Reasoner, _build_context_block
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
    avg_cost: float | None = None,
    market: MarketSnapshot | None = None,
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
        avg_cost=avg_cost,
        market=market,
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


def test_unrealized_pnl_is_computed_from_avg_cost() -> None:
    ctx = _context(avg_cost=100.0)   # precio 95 contra costo 100
    assert ctx.unrealized_pct == -5.0
    assert _context().unrealized_pct is None          # sin costo → desconocido
    assert _context(avg_cost=0.0).unrealized_pct is None  # costo 0 → no dividimos


def test_template_shows_loss_even_when_market_rose() -> None:
    # el caso MRVL: el papel sube en la ventana pero la posición está en rojo.
    ctx = _context(
        pct_change=4.5, verdict="Consolidar", action="consolidar", avg_cost=252.84
    )
    s = TemplateReasoner().generate(ctx)
    assert "📈" in s.text            # el mercado subió
    assert "EN PÉRDIDA" in s.text    # pero la posición no es una ganancia
    assert "252.84" in s.text


def test_prompt_separates_market_move_from_my_position() -> None:
    ctx = _context(pct_change=4.5, verdict="Consolidar", avg_cost=252.84)
    block = _build_context_block(ctx)
    assert "Movimiento del papel: +4.50%" in block
    assert "Mi posición: EN PÉRDIDA" in block


def test_prompt_omits_position_line_without_cost() -> None:
    assert "Mi posición" not in _build_context_block(_context())


def _market(
    down: int = 15, total: int = 21, benchmarks: tuple = (("S&P 500", -1.8),)
) -> MarketSnapshot:
    return MarketSnapshot(
        benchmarks=tuple(BenchmarkMove(label=lbl, pct_change=p) for lbl, p in benchmarks),
        breadth_down=down,
        breadth_total=total,
        window_minutes=390,
    )


def test_prompt_includes_market_context_when_present() -> None:
    # una caída acompañada por el mercado: el bloque lo tiene que decir.
    block = _build_context_block(_context(pct_change=-3.0, market=_market()))
    assert "Contexto de mercado: S&P 500 -1.8%" in block
    assert "15/21 de mis posiciones en rojo" in block


def test_prompt_omits_market_line_without_snapshot() -> None:
    assert "Contexto de mercado" not in _build_context_block(_context())


def test_template_shows_market_breadth() -> None:
    s = TemplateReasoner().generate(_context(pct_change=-3.0, market=_market()))
    assert "Contexto de mercado" in s.text
    assert "15/21" in s.text


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


def test_anthropic_client_uses_configured_retries() -> None:
    # el 529 que tumbó un /reevaluar: el cliente ahora reintenta más veces.
    s = Settings(_env_file=None, anthropic_api_key="sk-test", anthropic_max_retries=7)
    reasoner = AnthropicReasoner(s)
    assert reasoner._client.max_retries == 7


# ── ReasoningService (fallback) ──────────────────────────────────────────────
class _BoomReasoner:
    def generate(self, context: ReasoningContext) -> Suggestion:
        raise ReasoningError("boom")

    def review(self, context: PortfolioReviewContext) -> Suggestion:
        raise ReasoningError("boom")

    def plan(self, plan) -> Suggestion:  # noqa: ANN001
        raise ReasoningError("boom")


class _OkReasoner:
    def __init__(self, source: str) -> None:
        self._source = source

    def generate(self, context: ReasoningContext) -> Suggestion:
        return Suggestion(text=f"ok-{self._source}", source=self._source)

    def review(self, context: PortfolioReviewContext) -> Suggestion:
        return Suggestion(text=f"review-{self._source}", source=self._source)

    def plan(self, plan) -> Suggestion:  # noqa: ANN001
        return Suggestion(text=f"plan-{self._source}", source=self._source)


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


# ── Reevaluación integral (/reevaluar) ───────────────────────────────────────
def _review_context() -> PortfolioReviewContext:
    return PortfolioReviewContext(
        positions_block=(
            "• NVDA 18.0% [Mantener] — P/E 31.0, crec +70%\n"
            "• GOOG 12.0% [Crecer] — P/E 26.0, crec +17%"
        ),
        cash_block="• Satélite IA: $69 USD",
        total_value=23000.0,
        total_cash=564.0,
        position_count=2,
        note="posiciones pesadas → NVDA 18%",
    )


def test_template_review_dumps_positions_and_cash() -> None:
    s = TemplateReasoner().review(_review_context())
    assert s.source == "template"
    assert "NVDA 18.0%" in s.text
    assert "Satélite IA" in s.text
    assert "NVDA 18%" in s.text  # nota de concentración


def test_anthropic_review_builds_prompt_and_returns_text() -> None:
    client = _FakeClient(_Resp([_Block("Cartera sólida, NVDA pesa mucho.")]))
    reasoner = AnthropicReasoner(_settings(), client=client)

    s = reasoner.review(_review_context())

    assert s == Suggestion(text="Cartera sólida, NVDA pesa mucho.", source="anthropic")
    prompt = client.messages.last_kwargs["messages"][0]["content"]
    assert "NVDA 18.0%" in prompt and "Satélite IA" in prompt
    assert client.messages.last_kwargs["system"]  # system prompt de review


def test_service_review_falls_back_on_error() -> None:
    svc = ReasoningService(primary=_BoomReasoner(), fallback=_OkReasoner("template"))
    assert svc.review(_review_context()).source == "template"


# ── Planificador de cash (/plan) ─────────────────────────────────────────────
def _deployment_plan():
    from portfolio_monitor.planning import (
        AccountPlan,
        DeploymentCandidate,
        DeploymentPlan,
    )
    cand = DeploymentCandidate(
        ticker="GOOG", verdict="Crecer", weight=6.0, target_pct=9.0, gap_usd=300.0,
        price=200.0, market_value=1200.0, unrealized_pct=10.5, cluster="Hyperscaler",
        cluster_weight=7.1, pe=18.0, dip_threshold_pct=-3.4, tranche_usd=100.0,
        suggested_usd=300.0,
    )
    acct = AccountPlan("Satélite IA", "U1", 1000.0, 200.0, 800.0, 300.0, (cand,))
    return DeploymentPlan(1000.0, 800.0, 300.0, (acct,), targets_set=True)


def test_template_plan_returns_deterministic() -> None:
    s = TemplateReasoner().plan(_deployment_plan())
    assert s.source == "template"
    assert "Plan de despliegue de cash" in s.text
    assert "GOOG" in s.text


def test_anthropic_plan_builds_prompt_and_returns_text() -> None:
    client = _FakeClient(_Resp([_Block("Desplegá primero en GOOG.")]))
    s = AnthropicReasoner(_settings(), client=client).plan(_deployment_plan())
    assert s == Suggestion(text="Desplegá primero en GOOG.", source="anthropic")
    prompt = client.messages.last_kwargs["messages"][0]["content"]
    assert "GOOG" in prompt and "desplegar ~$300" in prompt
    assert client.messages.last_kwargs["system"]  # system prompt del plan


def test_service_plan_falls_back_on_error() -> None:
    svc = ReasoningService(primary=_BoomReasoner(), fallback=_OkReasoner("template"))
    assert svc.plan(_deployment_plan()).source == "template"


def test_review_prompt_includes_audit_flags() -> None:
    ctx = PortfolioReviewContext(
        positions_block="• MU 5.5% [Trim - tomar ganancias] — P/E 17.4, crec +167%",
        cash_block="",
        total_value=21000.0,
        total_cash=3000.0,
        position_count=1,
        audit_flags=("MU [Trim - tomar ganancias]: veredicto de recorte pero P/E 17",),
    )
    client = _FakeClient(_Resp([_Block("ok")]))
    AnthropicReasoner(_settings(), client=client).review(ctx)
    prompt = client.messages.last_kwargs["messages"][0]["content"]
    assert "Verdict audit" in prompt
    assert "veredicto de recorte pero P/E 17" in prompt


def test_review_prompt_includes_cluster_exposure() -> None:
    ctx = PortfolioReviewContext(
        positions_block="• NVDA 12% [Mantener] — P/E 30",
        cash_block="",
        total_value=21000.0,
        total_cash=3000.0,
        position_count=1,
        clusters=(ClusterExposure("Compute/GPU", 12.6, 2, -2.4),),
    )
    client = _FakeClient(_Resp([_Block("ok")]))
    AnthropicReasoner(_settings(), client=client).review(ctx)
    prompt = client.messages.last_kwargs["messages"][0]["content"]
    assert "Exposure by cluster" in prompt
    assert "Compute/GPU: 12.6%" in prompt
