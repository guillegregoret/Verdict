"""Tests del loader de contexto extra (.md) y su inyección en el system prompt."""

from __future__ import annotations

from pathlib import Path

from portfolio_monitor.config import Settings
from portfolio_monitor.reasoning.context_docs import load_context_docs


def test_missing_dir_returns_empty() -> None:
    assert load_context_docs("/no/existe/como/dir", 10000) == ""


def test_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert load_context_docs(str(tmp_path), 10000) == ""


def test_reads_and_orders_md_files(tmp_path: Path) -> None:
    (tmp_path / "10-b.md").write_text("Tesis B", encoding="utf-8")
    (tmp_path / "00-a.md").write_text("Tesis A", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("no soy md", encoding="utf-8")

    out = load_context_docs(str(tmp_path), 10000)

    assert "CONTEXTO ADICIONAL DEL USUARIO" in out  # header
    assert "Tesis A" in out and "Tesis B" in out
    assert "no soy md" not in out  # .txt ignorado
    assert out.index("Tesis A") < out.index("Tesis B")  # orden por nombre
    assert "## 00-a" in out and "## 10-b" in out  # encabezado por archivo


def test_readme_and_underscore_files_are_excluded(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("doc de la carpeta", encoding="utf-8")
    (tmp_path / "_notas.md").write_text("notas privadas", encoding="utf-8")
    (tmp_path / "20-real.md").write_text("contexto real", encoding="utf-8")

    out = load_context_docs(str(tmp_path), 10000)

    assert "contexto real" in out
    assert "doc de la carpeta" not in out  # README excluido
    assert "notas privadas" not in out  # prefijo _ excluido


def test_blank_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "vacio.md").write_text("   \n", encoding="utf-8")
    assert load_context_docs(str(tmp_path), 10000) == ""


def test_respects_max_chars(tmp_path: Path) -> None:
    (tmp_path / "grande.md").write_text("x" * 5000, encoding="utf-8")
    out = load_context_docs(str(tmp_path), 500)
    assert "…(recortado)" in out
    # el cuerpo recortado no supera el tope por mucho (header aparte).
    assert len(out) < 500 + 400


def test_reasoner_injects_context_into_system(tmp_path: Path) -> None:
    """El AnthropicReasoner anexa los .md al system prompt de generate."""
    # Import local para reusar los fakes/fixtures del test de reasoning.
    from test_reasoning import _Block, _context, _FakeClient, _Resp  # noqa: PLC0415

    (tmp_path / "estrategia.md").write_text(
        "Regla mía: priorizar infraponderados.", encoding="utf-8"
    )
    settings = Settings(
        _env_file=None,
        anthropic_model="claude-opus-5",
        strategy_context_dir=str(tmp_path),
    )
    from portfolio_monitor.reasoning import AnthropicReasoner  # noqa: PLC0415

    client = _FakeClient(_Resp([_Block("ok")]))
    AnthropicReasoner(settings, client=client).generate(_context())

    system = client.messages.last_kwargs["system"]
    assert "priorizar infraponderados" in system
    assert "READ-ONLY" in system  # el system base sigue presente
