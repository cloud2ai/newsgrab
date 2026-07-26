"""Shared test fixtures: an isolated local HTTP server for browser integration tests.

Used by test_browser.py and test_actions.py to serve fixture HTML/assets
without any real network access, and to observe which paths were actually
requested (e.g. to prove blocked resource types never reached the server).
"""
import http.server
import threading

import pytest


class _RoutedHandler(http.server.BaseHTTPRequestHandler):
    routes = {}
    request_log = []

    def do_GET(self):
        type(self).request_log.append(self.path)
        body = type(self).routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        content_type = "image/png" if self.path.endswith(".png") else (
            "text/css" if self.path.endswith(".css") else "text/html"
        )
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # keep test output quiet


class LocalHttpServer:
    def __init__(self, routes, request_log, base_url):
        self.routes = routes
        self.request_log = request_log
        self.base_url = base_url


@pytest.fixture
def local_http_server():
    """Serve routes registered on `.routes` for the test's duration.

    Usage:
        local_http_server.routes["/page.html"] = b"<html>...</html>"
        url = f"{local_http_server.base_url}/page.html"
        ...
        assert "/blocked.png" not in local_http_server.request_log
    """
    handler_cls = type("_TestHandler", (_RoutedHandler,), {"routes": {}, "request_log": []})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    handle = LocalHttpServer(
        routes=handler_cls.routes,
        request_log=handler_cls.request_log,
        base_url=f"http://127.0.0.1:{port}",
    )
    yield handle
    server.shutdown()
    thread.join()
