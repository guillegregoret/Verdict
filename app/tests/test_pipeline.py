"""Tests del AlertPipeline y el Scheduler con fakes (sin DB, red ni SDK)."""

from __future__ import annotations

from datetime import UTC, datetime

from portfolio_monitor.config import Settings
from portfolio_monitor.db.repositories import FundamentalsRow
from portfolio_monitor.notifier import NotifierError
from portfolio_monitor.reasoning import ReasoningContext, ReasoningError, Suggestion
from portfolio_monitor.scheduler import AlertPipeline, Scheduler
from portfolio_monitor.trigger import TriggerEvent


def _event(ticker: str = "NVDA") -> TriggerEvent:
    return TriggerEvent(
        ticker=ticker,
        pct_change=-5.2,
        window_minutes=390,
        reference_price=100.0,
        current_price=94.8,
        verdict="Mantener",
    )


class FakeTrigger:
    def __init__(self, events: list[TriggerEvent]) -> None:
        self._events = events

    def evaluate(self) -> list[TriggerEvent]:
        return list(self._events)


class FakeFundamentals:
    def __init__(self, row: FundamentalsRow | None = None, raises: bool = False) -> None:
        self._row = row
        self._raises = raises

    def latest(self, ticker: str) -> FundamentalsRow | None:
        if self._raises:
            raise RuntimeError("fundamentals boom")
        return self._row


class FakeReasoning:
    def __init__(self, error: bool = False) -> None:
        self._error = error
        self.contexts: list[ReasoningContext] = []

    def suggest(self, context: ReasoningContext) -> Suggestion:
        self.contexts.append(context)
        if self._error:
            raise ReasoningError("boom")
        return Suggestion(text=f"sugerencia {context.ticker}", source="template")


class FakeNotifier:
    def __init__(self, error: bool = False) -> None:
        self._error = error
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        if self._error:
            raise NotifierError("boom")
        self.sent.append(text)


class FakeAlerts:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **kwargs: object) -> int:
        self.records.append(dict(kwargs))
        return len(self.records)


def _pipeline(
    events: list[TriggerEvent],
    reasoning_error: bool = False,
    notifier_error: bool = False,
) -> tuple[AlertPipeline, FakeNotifier, FakeAlerts]:
    notifier = FakeNotifier(error=notifier_error)
    alerts = FakeAlerts()
    pipeline = AlertPipeline(
        trigger=FakeTrigger(events),
        fundamentals=FakeFundamentals(),
        reasoning=FakeReasoning(error=reasoning_error),
        notifier=notifier,
        alerts=alerts,
    )
    return pipeline, notifier, alerts


def test_run_once_sends_and_records() -> None:
    pipeline, notifier, alerts = _pipeline([_event("NVDA"), _event("GOOG")])

    assert pipeline.run_once() == 2
    assert notifier.sent == ["sugerencia NVDA", "sugerencia GOOG"]
    assert {r["ticker"] for r in alerts.records} == {"NVDA", "GOOG"}
    assert alerts.records[0]["suggestion"] == "sugerencia NVDA"


def test_run_once_no_events_is_noop() -> None:
    pipeline, notifier, alerts = _pipeline([])
    assert pipeline.run_once() == 0
    assert notifier.sent == []
    assert alerts.records == []


def test_reasoning_error_skips_event() -> None:
    pipeline, notifier, alerts = _pipeline([_event()], reasoning_error=True)
    assert pipeline.run_once() == 0
    assert notifier.sent == []
    assert alerts.records == []  # nada se notifica ni registra


def test_notifier_error_does_not_record_alert() -> None:
    # clave para el cooldown: si no se envió, no se registra
    pipeline, notifier, alerts = _pipeline([_event()], notifier_error=True)
    assert pipeline.run_once() == 0
    assert alerts.records == []


def test_fundamentals_reach_reasoning() -> None:
    # los fundamentals leídos deben llegar al contexto que ve el reasoner (§5.3)
    row = FundamentalsRow(
        ticker="NVDA", ts=datetime(2026, 7, 13, tzinfo=UTC), pe=20.0,
        revenue_growth=0.1, gross_margin=0.5, debt_to_equity=0.3,
    )
    reasoning = FakeReasoning()
    pipeline = AlertPipeline(
        trigger=FakeTrigger([_event("NVDA")]),
        fundamentals=FakeFundamentals(row=row),
        reasoning=reasoning,
        notifier=FakeNotifier(),
        alerts=FakeAlerts(),
    )
    assert pipeline.run_once() == 1
    assert reasoning.contexts[0].fundamentals is row


def test_fundamentals_error_does_not_break_alert() -> None:
    # best-effort: si el reader de fundamentals explota, la alerta igual sale
    reasoning = FakeReasoning()
    notifier = FakeNotifier()
    pipeline = AlertPipeline(
        trigger=FakeTrigger([_event("NVDA")]),
        fundamentals=FakeFundamentals(raises=True),
        reasoning=reasoning,
        notifier=notifier,
        alerts=FakeAlerts(),
    )
    assert pipeline.run_once() == 1
    assert notifier.sent == ["sugerencia NVDA"]
    assert reasoning.contexts[0].fundamentals is None


# ── Scheduler ────────────────────────────────────────────────────────────────
class FakePoller:
    def __init__(self) -> None:
        self.calls = 0

    def poll_once(self) -> int:
        self.calls += 1
        return 0


class FakePipeline:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> int:
        self.calls += 1
        return 0


def test_scheduler_tick_polls_then_runs_pipeline() -> None:
    poller, pipeline = FakePoller(), FakePipeline()
    scheduler = Scheduler(
        settings=Settings(_env_file=None), poller=poller, pipeline=pipeline
    )
    scheduler.tick()
    assert poller.calls == 1
    assert pipeline.calls == 1
