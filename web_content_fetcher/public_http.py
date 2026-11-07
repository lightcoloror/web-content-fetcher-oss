"""Opt-in public HTTP transport guards for bounded, unauthenticated capture."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.parse
import urllib.request


class PublicFetchBlocked(ValueError):
    pass


def public_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise PublicFetchBlocked('invalid_public_url') from exc
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            port not in {None, 80, 443} or any(ord(c) < 33 for c in url) or '\\' in url):
        raise PublicFetchBlocked('invalid_public_url')
    host = parsed.hostname.rstrip('.').lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise PublicFetchBlocked('non_public_host')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if '.' not in host:
            raise PublicFetchBlocked('non_public_host')
    else:
        if not address.is_global:
            raise PublicFetchBlocked('non_public_address')
    return url


def _public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    host, port = address
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(row[4][0] for row in answers))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise PublicFetchBlocked('non_public_dns_answer')
    # Connect to the validated numeric address. TLS still authenticates self.host.
    return socket.create_connection((addresses[0], port), timeout, source_address)


class _PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self._create_connection = _public_connection
        super().connect()


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        self._create_connection = _public_connection
        super().connect()


class _HTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        public_url(req.full_url)
        return self.do_open(_PublicHTTPConnection, req)


class _HTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        public_url(req.full_url)
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class _Redirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_open(request, *, timeout: float, context: ssl.SSLContext):
    public_url(request.full_url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _HTTP(),
                                         _HTTPS(context=context), _Redirect())
    return opener.open(request, timeout=timeout)
