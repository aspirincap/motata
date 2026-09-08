"""Backward-compatible rendering imports; implementation lives in common."""
from motata_cli.common.display import configure_output, print_output, _print_table, _stringify

__all__ = ["configure_output", "print_output"]
