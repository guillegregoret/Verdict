"""Carga de contexto extra del usuario (.md) para anexar al system prompt.

El usuario deja archivos Markdown con su estrategia, tesis o preferencias en
`strategy_context_dir`; se leen ordenados por nombre y se concatenan. Es material
de REFERENCIA que el usuario aporta para guiar las sugerencias (no datos de mercado
en vivo): en el prompt se aclara que ante un conflicto priman los datos del contexto
de la consulta. Best-effort: cualquier fallo de lectura no rompe el razonamiento.
"""

from __future__ import annotations

from pathlib import Path

from ..logging import get_logger

logger = get_logger(__name__)

_HEADER = (
    "\n\n---\n"
    "CONTEXTO ADICIONAL DEL USUARIO (estrategia, tesis y preferencias que el "
    "usuario aporta como lineamiento para las sugerencias). Es material de "
    "referencia, NO datos de mercado en vivo: usalo para entender objetivos y "
    "criterios, pero si algo acá contradice los datos del contexto de la consulta "
    "(precios, fundamentals, veredictos), PRIORIZÁ los datos y señalá la "
    "discrepancia.\n\n"
)


def load_context_docs(dir_path: str, max_chars: int) -> str:
    """Lee los .md de `dir_path` y los devuelve concatenados, o "" si no hay.

    Ordenados por nombre para dar control determinístico (ej. `00-`, `10-`).
    Se recorta a `max_chars` con aviso si se pasa. No lanza: ante cualquier error
    devuelve lo acumulado hasta el momento (o "").
    """
    try:
        base = Path(dir_path)
        if not base.is_dir():
            return ""
        # README.md documenta la carpeta y los archivos con prefijo `_` son notas
        # que el usuario no quiere en el prompt: se excluyen de la inyección.
        files = sorted(
            f
            for f in base.glob("*.md")
            if f.name.lower() != "readme.md" and not f.name.startswith("_")
        )
        if not files:
            return ""
        parts: list[str] = []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("Contexto: no pude leer %s (%s).", f.name, exc)
                continue
            if text:
                parts.append(f"## {f.stem}\n{text}")
        if not parts:
            return ""
        body = "\n\n".join(parts)
        if len(body) > max_chars:
            logger.warning(
                "Contexto extra %d chars > tope %d: se recorta.", len(body), max_chars
            )
            body = body[:max_chars].rstrip() + "\n…(recortado)"
        logger.info("Contexto extra: %d archivo(s), %d chars.", len(parts), len(body))
        return _HEADER + body
    except Exception as exc:  # noqa: BLE001 - best-effort, nunca romper el reasoner
        logger.warning("Contexto extra: fallo cargando (%s); sigo sin él.", exc)
        return ""
