"""Self-contained SSRF guard for playwright-service-resolved article URLs.

Rejects (returns False) non-http(s) schemes, missing netloc, embedded
credentials, localhost/.local hostnames, and any hostname or resolved IP
address that is private/loopback/link-local/reserved/multicast/non-global.
Never raises -- callers treat this as a plain boolean gate before parsing
content from a resolved URL.
"""
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_PRIVATE_HOSTNAMES = {"localhost", "localhost.localdomain"}


def _is_blocked_ip(ip: "ipaddress._BaseAddress") -> bool:
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return False
    if hostname in _PRIVATE_HOSTNAMES or hostname.endswith(".local"):
        return False

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None:
        return not _is_blocked_ip(ip)

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        logger.debug("[url_safety] DNS resolution failed for %s: %s", hostname, exc)
        return False

    if not addr_infos:
        return False

    has_public_address = False
    for info in addr_infos:
        try:
            resolved_ip = ipaddress.ip_address(info[4][0])
        except (IndexError, ValueError):
            continue
        if _is_blocked_ip(resolved_ip):
            return False
        has_public_address = True

    return has_public_address
