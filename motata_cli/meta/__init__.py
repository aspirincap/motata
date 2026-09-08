"""Meta namespace; importing a client/service must not initialize the CLI."""
from motata_cli.common.config import ensure_dirs
from motata_cli.common.errors import CliError


def register_meta_commands(subparsers):
    from .cli import register_meta_commands as register
    return register(subparsers)


__all__ = ["CliError", "ensure_dirs", "register_meta_commands"]
