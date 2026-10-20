"""
Hosts and CSRF origins that follow PUBLIC_BASE_URL.

Changing the domain used to mean editing production settings and redeploying,
which is a code change to record a fact that is already stored in the admin.

Two things make this safe rather than clever:

The environment list is a FLOOR, never replaced. If the database is
unreachable, or PUBLIC_BASE_URL has not been set yet, the configured hosts are
exactly what they were before this existed. A dynamic setting that can lock
everyone out of the site when a query fails is not worth the convenience.

And nothing here caches independently. `runtime.get` already holds a short
cache, so iterating these lists on a request is a dictionary lookup rather than
a query.
"""

from urllib.parse import urlsplit


def _configured_base() -> str:
    """
    The base URL from the admin, or "" when it is unset or unreachable.

    Swallows everything on purpose: this runs inside host validation, and an
    exception there turns every request into a 500 for a reason nobody would
    connect to a settings row.
    """
    try:
        from . import runtime

        return str(runtime.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    except Exception:
        return ""


def configured_host() -> str:
    """Just the hostname, no scheme and no port."""
    base = _configured_base()
    if not base:
        return ""
    return urlsplit(base).hostname or ""


def configured_origin() -> str:
    """Scheme and host, which is the shape CSRF_TRUSTED_ORIGINS wants."""
    base = _configured_base()
    if not base:
        return ""
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


class _Dynamic(list):
    """
    A list Django can iterate that resolves at the moment it is read.

    It genuinely subclasses `list`, which is not decoration: Django checks
    `isinstance(ALLOWED_HOSTS, (list, tuple))` while loading settings and
    refuses to start otherwise. The underlying list holds the floor, so even
    code that bypasses these methods entirely sees a working value.

    Django iterates ALLOWED_HOSTS inside `validate_host` on every request and
    reads CSRF_TRUSTED_ORIGINS inside the CSRF middleware, so a plain list is
    fixed at import time and this is not.
    """

    def __init__(self, floor, resolve):
        super().__init__(item for item in (floor or []) if item)
        self.resolve = resolve

    def _all(self) -> list:
        floor = list(super().__iter__())
        extra = self.resolve()
        if extra and extra not in floor:
            return floor + [extra]
        return floor

    def __iter__(self):
        return iter(self._all())

    def __contains__(self, item):
        return item in self._all()

    def __len__(self):
        return len(self._all())

    def __getitem__(self, index):
        return self._all()[index]

    def __bool__(self):
        return bool(self._all())

    def __repr__(self):
        return f"{type(self).__name__}({self._all()!r})"


def allowed_hosts(floor):
    return _Dynamic(floor, configured_host)


def trusted_origins(floor):
    return _Dynamic(floor, configured_origin)
