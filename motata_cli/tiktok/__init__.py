"""TikTok namespace with lazy CLI registration."""

def register_tiktok_commands(subparsers):
    from .commands import register_tiktok_commands as register
    return register(subparsers)


__all__ = ["register_tiktok_commands"]
