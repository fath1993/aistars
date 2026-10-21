"""
The domain lives in two files: the edge label in docker-compose.yml and
ALLOWED_HOSTS in aistars/.env. If they drift, the edge sends traffic that
Django refuses with 400, or Django allows a host nothing routes to.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
COMPOSE = ROOT / "docker-compose.yml"
ENV = ROOT / "aistars" / ".env"
NGINX_CONF = ROOT / "deploy" / "nginx" / "aistars.conf"


def label_hosts():
    return re.findall(r"routers\.aistars\.rule=Host\(`([^`]+)`\)", COMPOSE.read_text())


def env_hosts():
    match = re.search(r"^ALLOWED_HOSTS=(.*)$", ENV.read_text(), re.M)
    return [h.strip() for h in match.group(1).split(",") if h.strip()] if match else []


class EdgeRoutingTests(SimpleTestCase):
    def test_the_router_has_one_domain(self):
        self.assertEqual(len(label_hosts()), 1)

    def test_the_routed_domain_is_an_allowed_host(self):
        if not ENV.is_file():
            self.skipTest("no .env on disk")
        self.assertIn(label_hosts()[0], env_hosts())

    def test_the_router_uses_the_edge_contract(self):
        text = COMPOSE.read_text()
        self.assertIn("traefik.http.routers.aistars.entrypoints=web\n", text)
        # Cloudflare does HTTPS; the edge has no certificate resolver, and a
        # router asking for one would never be served.
        self.assertNotIn("certresolver", text)

    def test_the_project_nginx_passes_the_edge_scheme_through(self):
        # $scheme would always be http inside the stack, and Django would treat
        # every request as insecure.
        text = NGINX_CONF.read_text()
        self.assertIn("X-Forwarded-Proto $http_x_forwarded_proto", text)
        self.assertNotIn("X-Forwarded-Proto $scheme", text)

    def test_the_project_nginx_finds_the_app_at_every_request(self):
        # A hostname in proxy_pass is looked up once, when nginx starts; a
        # recreated app container on a new address then gets nothing but 502s.
        text = NGINX_CONF.read_text()
        self.assertIn("resolver 127.0.0.11", text)
        self.assertIn("set $app http://aistars:8000;", text)
        self.assertIn("proxy_pass $app;", text)
