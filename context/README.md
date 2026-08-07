# Contexto extra para el razonamiento (Claude)

Dejá acá tus archivos `.md` con estrategia, tesis, criterios o preferencias.
Se leen **en cada consulta** y se anexan al *system prompt* de los tres flujos
que razonan con Claude:

- alertas de precio/fundamentals (`generate`)
- `/reevaluar` (`review`)
- `/plan` (`plan`)

## Cómo funciona

- Se toman **todos los `.md`** de esta carpeta, **ordenados por nombre**. Usá
  prefijos numéricos para controlar el orden (`00-estrategia.md`, `10-tesis-nvda.md`).
- Este `README.md` también se incluye — si no querés que entre al prompt,
  renombralo (ej. `_README.txt`) o borralo.
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
