"""Shared test setup. No test touches the network: only loopback (the browser test's own server) is allowed."""

import socket
from urllib.parse import urlparse

import pytest
import requests

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_request = requests.Session.request
    real_connect = socket.create_connection

    def guarded_request(self, method, url, *args, **kwargs):
        if urlparse(url).hostname not in LOOPBACK:
            raise RuntimeError("network disabled in tests")
        return real_request(self, method, url, *args, **kwargs)

    def guarded_connect(address, *args, **kwargs):
        if address[0] not in LOOPBACK:
            raise RuntimeError("network disabled in tests")
        return real_connect(address, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", guarded_request)
    monkeypatch.setattr(socket, "create_connection", guarded_connect)
