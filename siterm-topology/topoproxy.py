#!/usr/bin/env python3
"""Serve SiteRM Archify topology pages to Grafana, fetched with the oidcgen token of each site."""

# Runs as a sidecar of the Prometheus pod and reads the rm-configs checkout oidcgen keeps in a shared volume:
# each site directory names its Frontends, and oidcgen writes that directory's token to oidc-<dir>.token.
# Grafana reaches it only through its data source proxy, which requires a logged-in user and
# sandboxes the response without scripts.

import base64
import hmac
import html
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from yaml import YAMLError, safe_load

REPO_DIR = os.environ.get("TOPO_REPO_DIR", "/siterm-repo/rm-configs")
TOKEN_DIR = os.environ.get("TOPO_TOKEN_DIR", "/etc/oidc")
RESCAN = int(os.environ.get("TOPO_RESCAN", "120"))
PORT = int(os.environ.get("TOPO_PORT", "9098"))
CACHE_TTL = int(os.environ.get("TOPO_CACHE_TTL", "300"))
ERROR_TTL = int(os.environ.get("TOPO_ERROR_TTL", "60"))
TIMEOUT = int(os.environ.get("TOPO_TIMEOUT", "90"))
MAX_BYTES = 16 * 1024 * 1024
MAX_PATH_ID = 1024
# Grafana's data source authenticates with basic auth; without it any pod that can reach the
# service could read every site's topology. Stripped: secrets created --from-file keep the file's newline.
AUTH_USER = os.environ.get("TOPO_USER", "").strip()
AUTH_PASSWORD = os.environ.get("TOPO_PASSWORD", "").strip()
CA_FILE = os.environ.get("TOPO_CA_FILE") or None

REQUEST_PATH = re.compile(r"^/topology/([A-Za-z0-9_.-]{1,128})$")
RESPONSE_HEADERS = {
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=60",
}
# Scripts never run behind Grafana's proxy, so hide the viewer controls that would do nothing.
STATIC_STYLE = "<style>.toolbar, .diagram-nav { display: none !important; }</style>"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the bearer token is never sent to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl} refused", headers, fp)


SSL_CONTEXT = ssl.create_default_context(cafile=CA_FILE)
OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=SSL_CONTEXT))


def _loadYaml(fname):
    """Load a YAML file, or return None if it is missing or invalid (one bad site config must not hide the others)."""
    try:
        with open(fname, "r", encoding="utf-8") as fd:
            return safe_load(fd)
    except (OSError, YAMLError) as ex:
        print(f"Skipping {fname}: {ex}", flush=True)
        return None


def _siteNames(conf):
    """Return the site names a Frontend config serves (general.sites, or the older general.sitename)."""
    general = conf.get("general") or {}
    names = general.get("sites") or general.get("sitename") or []
    return [names] if isinstance(names, str) else [name for name in names if isinstance(name, str)]


def _frontendUrl(webdomain):
    """Return the https base URL of a Frontend webdomain, or None (the token is never sent over plain http)."""
    webdomain = str(webdomain or "").strip().rstrip("/")
    if not webdomain or webdomain.startswith("http://"):
        return None
    if not webdomain.startswith("https://"):
        webdomain = f"https://{webdomain}"
    host = urllib.parse.urlsplit(webdomain).hostname
    return None if not host or host.startswith("127.") else webdomain


def loadSites(repoDir, tokenDir):
    """Return {site: (Frontend base URL, token file)} for every enabled Frontend in an rm-configs checkout.

    Matches oidcgen: it mints one token per site directory that has a Frontend and saves it as oidc-<dir>.token.
    """
    sites = {}
    for dirName in sorted(os.listdir(repoDir)):
        siteDir = os.path.join(repoDir, dirName)
        mapping = os.path.join(siteDir, "mapping.yaml")
        if dirName.startswith(".") or os.path.isfile(os.path.join(siteDir, "disabled")) or not os.path.isfile(mapping):
            continue
        tokenFile = os.path.join(tokenDir, f"oidc-{dirName.lower()}.token")
        entries = _loadYaml(mapping)
        for entry in entries.values() if isinstance(entries, dict) else []:
            if not isinstance(entry, dict) or entry.get("type") != "FE" or not entry.get("config"):
                continue
            conf = _loadYaml(os.path.join(siteDir, str(entry["config"]), "main.yaml"))
            base = _frontendUrl((conf.get("general") or {}).get("webdomain")) if isinstance(conf, dict) else None
            for site in _siteNames(conf) if base else []:
                sites.setdefault(site, (base, tokenFile))
    return sites


class SiteMap:
    """Site lookup rebuilt from the rm-configs checkout at most every RESCAN seconds."""

    # pylint: disable=too-few-public-methods

    def __init__(self, repoDir, tokenDir):
        self.repoDir = repoDir
        self.tokenDir = tokenDir
        self.nextScan = 0
        self.sites = {}
        self.lock = threading.Lock()

    def get(self, site):
        """Return (Frontend base URL, token file) for a site, or None."""
        with self.lock:
            if time.time() >= self.nextScan:
                try:
                    self.sites = loadSites(self.repoDir, self.tokenDir)
                except OSError as ex:
                    print(f"Keeping previous site list, cannot read {self.repoDir}: {ex}", flush=True)
                # Until oidcgen has cloned the repo, look again soon.
                self.nextScan = time.time() + (RESCAN if self.sites else 10)
            return self.sites.get(site)


def fetchTopology(base, site, tokenFile, pathId):
    """Fetch the Archify topology page of one site from its Frontend."""
    with open(tokenFile, "r", encoding="utf-8") as fd:
        token = fd.read().strip()
    # Graph only: the legend, health and link cards stay on the SiteRM web UI.
    query = {"view": "graph", **({"path": pathId} if pathId else {})}
    url = f"{base}/api/{urllib.parse.quote(site)}/topo/archify?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "text/html"})
    with OPENER.open(req, timeout=TIMEOUT) as resp:
        body = resp.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("topology page is larger than 16MB")
    return body.decode("utf-8", errors="replace").replace("</head>", STATIC_STYLE + "</head>", 1).encode()


def errorPage(site, message):
    """Return a small page that tells the dashboard viewer why there is no diagram."""
    text = html.escape(f"Topology for {site} is not available: {message}")
    style = "margin:16px;font-family:sans-serif;color:#9aa3b2;background:#181b1f"
    return f'<!doctype html><html><body style="{style}"><p>{text}</p></body></html>'.encode()


def _frontendError(ex):
    """Describe a Frontend HTTP error without echoing large or untrusted bodies."""
    if ex.code in (401, 403):
        return f"the Frontend rejected the monitoring token (HTTP {ex.code})"
    if ex.code == 404:
        return "the Frontend has no topology yet, or runs a SiteRM version without /topo/archify (HTTP 404)"
    if ex.code == 422:
        return "the Frontend runs a SiteRM version without the graph view of /topo/archify (HTTP 422)"
    return f"the Frontend returned HTTP {ex.code}"


class TopologyCache:
    """Per (site, path) page cache; failures are cached briefly so a broken Frontend is not hammered."""

    # pylint: disable=too-few-public-methods

    def __init__(self, sites):
        self.sites = sites
        self.entries = {}
        self.lock = threading.Lock()

    def get(self, site, pathId):
        """Return (HTTP status, page) for a site and optional active path id."""
        key = (site, pathId)
        with self.lock:
            entry = self.entries.get(key)
            if entry and entry[0] > time.time():
                return entry[1], entry[2]
        target = self.sites.get(site)
        if target is None:
            return 404, errorPage(site, "it is not a monitored SiteRM site")
        status, ttl = 200, CACHE_TTL
        if not os.path.isfile(target[1]):
            return 503, errorPage(site, "oidcgen has no token for this site yet (is its Frontend reachable?)")
        try:
            page = fetchTopology(target[0], site, target[1], pathId)
        except urllib.error.HTTPError as ex:
            status, ttl, page = 502, ERROR_TTL, errorPage(site, _frontendError(ex))
        except (OSError, ValueError) as ex:
            status, ttl, page = 502, ERROR_TTL, errorPage(site, f"could not reach the Frontend ({type(ex).__name__})")
        with self.lock:
            self.entries = {k: v for k, v in self.entries.items() if v[0] > time.time()}
            self.entries[key] = (time.time() + ttl, status, page)
        return status, page


def authorized(header):
    """Return True if the Authorization header has the configured basic auth credentials (or none are set)."""
    if not AUTH_PASSWORD:
        return True
    expected = "Basic " + base64.b64encode(f"{AUTH_USER}:{AUTH_PASSWORD}".encode()).decode("ascii")
    return hmac.compare_digest((header or "").encode(), expected.encode())


class Handler(BaseHTTPRequestHandler):
    """GET /topology/<site>[?path=<active connection id>] and /healthz."""

    cache = None

    def _send(self, status, body, contentType="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", contentType)
        for key, val in RESPONSE_HEADERS.items():
            self.send_header(key, val)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # pylint: disable=invalid-name
        """Serve one request."""
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/healthz":
            self._send(200, b"ok", "text/plain")
            return
        if not authorized(self.headers.get("Authorization")):
            self._send(401, b"unauthorized", "text/plain")
            return
        match = REQUEST_PATH.match(url.path)
        pathId = urllib.parse.parse_qs(url.query).get("path", [""])[0]
        if not match or len(pathId) > MAX_PATH_ID:
            self._send(404, b"not found", "text/plain")
            return
        self._send(*self.cache.get(match.group(1), pathId))


def main():
    """Start the server."""
    if not AUTH_PASSWORD:
        print("WARNING: TOPO_PASSWORD is not set; the topology service accepts unauthenticated requests.", flush=True)
    Handler.cache = TopologyCache(SiteMap(REPO_DIR, TOKEN_DIR))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
