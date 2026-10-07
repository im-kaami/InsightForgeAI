import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from insightforge.ingest.base import IngestError

_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}
_BLOCKED_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
}


def resolve_host(hostname: str) -> list[str]:
    try:
        return sorted({item[4][0] for item in socket.getaddrinfo(hostname, None)})
    except OSError as error:
        raise IngestError("Could not resolve host") from error


def validate_public_url(
    url: str,
    *,
    allow_private: bool = False,
    resolver: Callable[[str], list[str]] | None = None,
) -> None:
    if allow_private:
        return
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise IngestError("URL scheme must be http or https")
    hostname = parsed.hostname
    if not hostname:
        raise IngestError("URL must include a hostname")
    check_public_host(hostname, "Private or internal URLs are not allowed", resolver)


def check_public_host(
    hostname: str,
    message: str,
    resolver: Callable[[str], list[str]] | None = None,
) -> None:
    """Refuse a host that is, or resolves to, a private, local or otherwise non-public address."""
    lowered = hostname.lower().rstrip(".")
    if (
        lowered in _BLOCKED_HOSTS
        or lowered.endswith(".localhost")
        or lowered.endswith(".internal")
        or lowered.endswith(".local")
    ):
        raise IngestError(message)
    try:
        addresses = [lowered] if ipaddress.ip_address(lowered) else []
    except ValueError:
        try:
            addresses = (resolver or resolve_host)(lowered)
        except IngestError:
            raise
        except OSError as error:
            raise IngestError("Could not resolve host") from error
    if not addresses:
        raise IngestError("Could not resolve host")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise IngestError("Could not resolve host") from error
        if address in _BLOCKED_ADDRESSES or not address.is_global or address.is_multicast:
            raise IngestError(message)
