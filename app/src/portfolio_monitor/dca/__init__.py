"""DCA: sizing sugerido de compra en dips, capado al cash disponible (§5.4).

Read-only: sugiere cuánto comprar; el usuario ejecuta en IBKR. Nunca opera.
"""

from .service import DcaSizer, DcaSuggestion

__all__ = ["DcaSizer", "DcaSuggestion"]
