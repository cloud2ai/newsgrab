"""Tests for the SSRF guard. IP-literal cases must never trigger real DNS
resolution -- verified by asserting socket.getaddrinfo is never called."""
from unittest.mock import patch

from app.url_safety import is_safe_url


def test_public_https_url_is_safe():
    assert is_safe_url("https://example.com/article") is True


def test_private_ip_literal_is_unsafe():
    assert is_safe_url("http://10.0.0.5/x") is False


def test_localhost_hostname_is_unsafe():
    assert is_safe_url("http://localhost/x") is False


def test_loopback_ip_literal_is_unsafe():
    assert is_safe_url("http://127.0.0.1/x") is False


def test_malformed_url_without_scheme_is_unsafe():
    assert is_safe_url("not-a-url") is False


def test_url_with_credentials_is_unsafe():
    assert is_safe_url("https://user:pass@example.com/x") is False


def test_ip_literal_checks_never_trigger_dns_resolution():
    with patch("socket.getaddrinfo") as mock_getaddrinfo:
        is_safe_url("http://10.0.0.5/x")
        is_safe_url("http://127.0.0.1/x")
        mock_getaddrinfo.assert_not_called()


def test_dns_resolution_failure_is_unsafe():
    with patch("socket.getaddrinfo", side_effect=OSError("resolution failed")):
        assert is_safe_url("http://this-does-not-resolve.invalid/x") is False
