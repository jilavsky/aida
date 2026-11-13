"""Re-export: the coercion now lives in ``aida.core.argument_coercion``
because the agent loop applies it to native tools too, and ``core`` must
not import from ``mcp``. Kept so existing imports keep working."""

from aida.core.argument_coercion import coerce_arguments

__all__ = ["coerce_arguments"]
