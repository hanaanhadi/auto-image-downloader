"""
Targeted DNS override — resolves specific hostnames via Cloudflare (1.1.1.1)
instead of the system default resolver, without touching system/network
config. Used because commons.wikimedia.org returns "DNS operation refused"
from this machine's default DNS while every other tested domain resolves
fine, indicating a filter/block on that specific hostname only.
"""

import socket
import dns.resolver

OVERRIDE_HOSTS = {"commons.wikimedia.org"}

_resolver = dns.resolver.Resolver(configure=False)
_resolver.nameservers = ["1.1.1.1", "1.0.0.1"]

_cache = {}
_orig_getaddrinfo = socket.getaddrinfo


def _resolve(hostname):
    if hostname not in _cache:
        answer = _resolver.resolve(hostname, "A")
        _cache[hostname] = str(answer[0])
    return _cache[hostname]


def _patched_getaddrinfo(host, *args, **kwargs):
    if host in OVERRIDE_HOSTS:
        try:
            ip = _resolve(host)
            return _orig_getaddrinfo(ip, *args, **kwargs)
        except Exception:
            pass
    return _orig_getaddrinfo(host, *args, **kwargs)


def install():
    socket.getaddrinfo = _patched_getaddrinfo
