"""
Middleware that lets settings follow the admin rather than the code.
"""

from collections import defaultdict
from urllib.parse import urlsplit

from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware

class DynamicCsrfViewMiddleware(CsrfViewMiddleware):
    """
    Django's CSRF middleware reads CSRF_TRUSTED_ORIGINS through three
    `cached_property` attributes, computed once per process. A dynamic setting
    is therefore invisible to it — the list would resolve correctly and be read
    exactly once, at the first request after a restart.

    These are the same three, as plain properties. That is the whole change.
    """

    @property
    def csrf_trusted_origins_hosts(self):
        return [urlsplit(origin).netloc.lstrip("*")
                for origin in settings.CSRF_TRUSTED_ORIGINS]

    @property
    def allowed_origins_exact(self):
        return {origin for origin in settings.CSRF_TRUSTED_ORIGINS
                if "*" not in origin}

    @property
    def allowed_origin_subdomains(self):
        allowed = defaultdict(list)
        for parsed in (urlsplit(origin) for origin in settings.CSRF_TRUSTED_ORIGINS
                       if "*" in origin):
            allowed[parsed.scheme].append(parsed.netloc.lstrip("*"))
        return allowed
