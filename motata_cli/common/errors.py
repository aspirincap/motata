"""Platform-independent CLI failures."""
from __future__ import annotations


class CliError(RuntimeError):
    def __init__(self, *args: object, exit_code: int = 1) -> None:
        super().__init__(*args)
        self.exit_code = exit_code
