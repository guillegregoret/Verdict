"""Tests de configuración (puros, sin infra)."""

from portfolio_monitor.config import Settings


def test_sqlalchemy_url_rewrites_driver() -> None:
    s = Settings(_env_file=None, database_url="postgresql://u:p@h:5432/db")
    assert s.sqlalchemy_url == "postgresql+psycopg://u:p@h:5432/db"


def test_sqlalchemy_url_leaves_explicit_driver_untouched() -> None:
    url = "postgresql+psycopg://u:p@h:5432/db"
    s = Settings(_env_file=None, database_url=url)
    assert s.sqlalchemy_url == url


def test_defaults_are_sane() -> None:
    s = Settings(_env_file=None)
    assert s.poll_interval_seconds > 0
    assert s.finnhub_request_spacing_seconds >= 0
