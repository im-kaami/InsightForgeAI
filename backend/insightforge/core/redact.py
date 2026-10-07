import re

_ROOTS = r"[A-Za-z]:[\\/]|\\\\\?\\|/(?:home|users|var|tmp|app|data|etc|opt|usr|mnt)/"
_PATH = re.compile(rf"""(?:{_ROOTS})[^\s'"),;:]*""", re.IGNORECASE)


def clean_error(error: object, limit: int = 400) -> str:
    """An error message that is safe to show: on one line, shortened, server file paths removed."""
    text = " ".join(str(error).split())
    return _PATH.sub("<path>", text)[:limit]
