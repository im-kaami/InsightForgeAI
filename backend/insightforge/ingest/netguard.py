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


def pin_public_url(url: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Resolve the host once, check every address, and return (url with the IP, headers, extensions).

    Connecting to the checked address closes the DNS rebinding gap between the check and the request.
    The Host header and the TLS server name keep the original host name.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise IngestError("URL must be http or https and include a hostname")
    message = "Private or internal URLs are not allowed"
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        addresses = resolve_host(hostname.lower().rstrip("."))
        check_public_host(hostname, message, lambda _name: addresses)
        address = addresses[0]
    else:
        check_public_host(hostname, message)
        return url, {}, {}
    ip = ipaddress.ip_address(address)
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    netloc = host + (f":{parsed.port}" if parsed.port else "")
    pinned = parsed._replace(netloc=netloc).geturl()
    default_port = {"http": 80, "https": 443}[parsed.scheme.lower()]
    host_header = hostname + (f":{parsed.port}" if parsed.port and parsed.port != default_port else "")
    return pinned, {"Host": host_header}, {"sni_hostname": hostname}


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
