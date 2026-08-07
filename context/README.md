# Contexto extra para el razonamiento (Claude)

Dejá acá tus archivos `.md` con estrategia, tesis, criterios o preferencias.
Se leen **en cada consulta** y se anexan al *system prompt* de los tres flujos
que razonan con Claude:

- alertas de precio/fundamentals (`generate`)
- `/reevaluar` (`review`)
- `/plan` (`plan`)

## Archivos actuales

- `00-perfil-y-disciplina.md` — perfil del inversor, disciplina (6 reglas) y marcos
  de decisión (tesis intacta vs rota, sumar vs recortar, DCA, concentración, moneda,
  tono de salida).
- `10-tesis-por-nombre.md` — tesis durable por nombre de las dos cuentas + watchlist
  de defensa/óptica.

## Cómo funciona

- Se toman los `.md` de esta carpeta, **ordenados por nombre**. Usá prefijos
  numéricos para controlar el orden (`00-…`, `10-…`, `20-…`).
- **Se excluyen del prompt:** este `README.md` y cualquier archivo con prefijo `_`
  (ej. `_notas.md`, o una subcarpeta `_source/`). Usalos para notas que no querés
  que Claude lea.
- Es material de **referencia/lineamiento**, no datos de mercado en vivo. Si algo
  acá contradice los datos de la consulta (precios, fundamentals, veredictos),
  el prompt le indica a Claude **priorizar los datos** y señalar la discrepancia.
- Tope total: **24.000 caracteres** (`STRATEGY_CONTEXT_MAX_CHARS`). Si te pasás,
  se recorta y queda un aviso en los logs.
- **Editable sin rebuild**: la carpeta está montada read-only en el container
  (`./context` → `/app/context`). Editás el `.md`, y la próxima consulta ya lo usa.

## Seguridad

Read-only para el container; Claude nunca ejecuta lo que digan estos archivos
(el sistema es read-only y no opera). Tratá el contenido como tus notas de
estrategia, no como comandos.
