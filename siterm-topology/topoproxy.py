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
from collections import deque
from html.parser import HTMLParser
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
# Archify's click-to-inspect panel is built by its viewer script, which cannot run here. addDetails() adds a static
# panel per device and link instead; clicking focuses the element (devices already have tabindex) and CSS shows it.
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
LINK_GROUP = re.compile(r'<g data-siterm-link="(e\d+)">(.*?)</g>', re.DOTALL)
# Archify's own edge drawing, kept when SiteRM did not redraw a device pair.
PLAIN_EDGE = re.compile(r'<path (data-edge-from="[^"]*" data-edge-to="[^"]*"[^>]*?\sdata-edge-id="(e\d+)"[^>]*)/>')
DETAILS_STYLE = """
svg [data-node-id], svg [data-siterm-link], svg path[data-edge-id][tabindex] { cursor: pointer; }
svg [data-node-id]:focus, svg [data-siterm-link]:focus, svg path[data-edge-id]:focus {
  outline: none; filter: drop-shadow(0 0 6px var(--frontend-stroke));
}
svg .st-hit { fill: none; stroke: transparent; stroke-width: 12; pointer-events: stroke; }
.st-detail {
  display: none; position: fixed; top: 12px; left: 12px; z-index: 50; box-sizing: border-box;
  width: min(340px, calc(100vw - 24px)); max-height: calc(100vh - 24px); overflow: auto; padding: 12px 14px;
  background: var(--toolbar-menu-bg, var(--bg)); border: 1px solid var(--panel-border); border-radius: 10px; color: var(--text);
  font-size: 12px; line-height: 1.4; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35); outline: none;
}
.st-detail:hover, .st-detail:focus { display: block; }
.st-detail .st-kind { color: var(--frontend-stroke); font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; }
.st-detail h4 { margin: 2px 0; font-size: 15px; overflow-wrap: anywhere; }
.st-detail h5 { margin: 12px 0 4px; color: var(--text-muted); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; }
.st-detail p { margin: 0; color: var(--text-muted); overflow-wrap: anywhere; }
.st-detail ul { margin: 0; padding: 0; list-style: none; }
.st-detail li { padding: 4px 0; border-top: 1px solid var(--panel-border); overflow-wrap: anywhere; }
.st-detail li small { display: block; color: var(--text-muted); }
.st-detail .st-reach { display: flex; gap: 8px; margin-top: 10px; }
.st-detail .st-reach span { flex: 1; padding: 4px 8px; border: 1px solid var(--panel-border); border-radius: 6px; }
.st-detail .st-reach b { float: right; }
.st-detail .st-hint { margin-top: 10px; color: var(--text-dim); font-size: 11px; }
"""


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


class DiagramParser(HTMLParser):
    """Collect the devices, links (with their SiteRM tooltips) and legend labels of an Archify diagram's SVG."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = {}
        self.edges = {}
        self.kinds = {}
        self._edge = None
        self._title = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        nodeId, edgeId = attrs.get("data-node-id") or "", attrs.get("data-edge-id") or ""
        if tag == "g" and SAFE_ID.match(nodeId):
            node = {"label": attrs.get("data-node-label") or nodeId, "sublabel": attrs.get("data-node-sublabel") or "", "kind": attrs.get("data-node-kind") or ""}
            self.nodes.setdefault(nodeId, node)
        elif attrs.get("data-legend-kind") and attrs.get("data-legend-label"):
            self.kinds.setdefault(attrs["data-legend-kind"], attrs["data-legend-label"])
        elif SAFE_ID.match(edgeId) and attrs.get("data-edge-from") and attrs.get("data-edge-to"):
            edge = self.edges.setdefault(edgeId, {"from": attrs["data-edge-from"], "to": attrs["data-edge-to"], "lines": [], "bundles": []})
            # SiteRM draws one path per physical link and port-channel rings as ellipses, each with a <title>.
            self._edge = (tag, edge["bundles"] if tag == "ellipse" else edge["lines"])
        elif tag == "title" and self._edge:
            self._title = []

    def handle_data(self, data):
        if self._title is not None:
            self._title.append(data)

    def handle_endtag(self, tag):
        if tag == "title" and self._title is not None:
            text = " ".join("".join(self._title).split())
            if text and text not in self._edge[1]:
                self._edge[1].append(text)
            self._title = None
        elif self._edge and tag == self._edge[0]:
            self._edge = None


def _plural(count, noun):
    """Return '1 link' or '3 links'."""
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _reach(start, adjacency):
    """Return how many devices are reachable from start along the diagram's edge direction."""
    seen, queue = {start}, deque([start])
    while queue:
        for nxt in adjacency.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return len(seen) - 1


def _items(rows):
    """Return a <ul> of (bold title, [detail lines]) rows; all text is escaped."""
    lis = []
    for title, details in rows:
        head = f"<b>{html.escape(title)}</b>" if title else ""
        lis.append("<li>" + head + "".join(f"<small>{html.escape(line)}</small>" for line in details) + "</li>")
    return "<ul>" + "".join(lis) + "</ul>"


def _panel(panelId, kind, title, subtitle, body):
    """Return one hidden details panel; tabindex keeps it open while it is clicked (see DETAILS_STYLE)."""
    return (
        f'<div class="st-detail" id="{panelId}" tabindex="-1" role="region" aria-label="{html.escape(title)} details">'
        f'<div class="st-kind">{html.escape(kind)}</div><h4>{html.escape(title)}</h4><p>{html.escape(subtitle)}</p>{body}'
        '<p class="st-hint">Click outside this panel to close it.</p></div>'
    )


def _focusRules(focused, panelId, keep):
    """Return the CSS that shows a panel while its element has focus and dims the devices and links not in keep."""
    selectors = [f'[data-node-id="{nodeId}"]' for nodeId in keep["nodes"]] + [f'[data-siterm-link="{e}"], [data-edge-id="{e}"]' for e in keep["edges"]]
    when = f"body:has({focused}:focus)"
    dimmed = "svg :is([data-node-id], [data-siterm-link], path[data-edge-id][tabindex])"
    return f"{when} #{panelId} {{ display: block; }}\n{when} {dimmed}:not({', '.join(selectors)}) {{ opacity: 0.25; }}"


def _nodeDetails(nodeId, diagram, adjacency):
    """Return the panel and CSS for one device: like Archify's own panel, with the SiteRM link details of each neighbour."""
    nodes, edges = diagram["nodes"], diagram["edges"]
    node = nodes[nodeId]
    panelId = f"st-d-n-{nodeId}"
    body = (
        f'<div class="st-reach"><span>Upstream <b>{_reach(nodeId, adjacency["up"])}</b></span>'
        f"<span>Downstream <b>{_reach(nodeId, adjacency['down'])}</b></span></div>"
    )
    keep = {"nodes": [nodeId], "edges": []}
    for heading, own, other in (("Outgoing", "from", "to"), ("Incoming", "to", "from")):
        rows = [(edgeId, edge) for edgeId, edge in edges.items() if edge[own] == nodeId]
        if rows:
            body += f"<h5>{heading} &middot; {len(rows)}</h5>" + _items((nodes[edge[other]]["label"], edge["lines"] + edge["bundles"]) for _, edge in rows)
        keep["nodes"] += [edge[other] for _, edge in rows]
        keep["edges"] += [edgeId for edgeId, _ in rows]
    kind = diagram["kinds"].get(node["kind"], node["kind"] or "Device")
    return _panel(panelId, kind, node["label"], node["sublabel"], body), _focusRules(f'svg [data-node-id="{nodeId}"]', panelId, keep)


def _edgeDetails(edgeId, diagram):
    """Return the panel and CSS for one device pair: its endpoints, physical links and port-channels."""
    nodes, edge = diagram["nodes"], diagram["edges"][edgeId]
    ends = (nodes[edge["from"]], nodes[edge["to"]])
    panelId = f"st-d-e-{edgeId}"
    subtitle = ", ".join([_plural(len(edge["lines"]), "link")] + ([_plural(len(edge["bundles"]), "port-channel")] if edge["bundles"] else []))
    body = "<h5>Endpoints</h5>" + _items((end["label"], [end["sublabel"]] if end["sublabel"] else []) for end in ends)
    if edge["lines"]:
        body += f"<h5>Links &middot; {len(edge['lines'])}</h5>" + _items(("", [line]) for line in edge["lines"])
    if edge["bundles"]:
        body += f"<h5>Port-channels &middot; {len(edge['bundles'])}</h5>" + _items(("", [bundle]) for bundle in edge["bundles"])
    keep = {"nodes": [edge["from"], edge["to"]], "edges": [edgeId]}
    focused = f':is([data-siterm-link="{edgeId}"], path[data-edge-id="{edgeId}"][tabindex])'
    return _panel(panelId, "Link", f"{ends[0]['label']} ↔ {ends[1]['label']}", subtitle, body), _focusRules(focused, panelId, keep)


def _focusableLink(match, diagram):
    """Make one SiteRM link group focusable, with a wider invisible stroke under its thin lines so it is easy to click."""
    edgeId, inner = match.group(1), match.group(2)
    edge = diagram["edges"].get(edgeId)
    if edge is None:
        return match.group(0)
    hits = "".join(f'<path class="st-hit" d="{d}"/>' for d in re.findall(r'<path [^>]*?\sd="([^"]*)"', inner))
    label = f"Details of the links between {diagram['nodes'][edge['from']]['label']} and {diagram['nodes'][edge['to']]['label']}"
    return f'<g data-siterm-link="{edgeId}" tabindex="0" role="button" aria-label="{html.escape(label)}">{hits}{inner}</g>'


def addDetails(page):
    """Add a static details panel for every device and link of an Archify page, shown on click without any script.

    Returns the page unchanged if it has no diagram this understands (for example an older SiteRM version).
    """
    svgStart = page.find("<svg viewBox")
    svgEnd = page.find("</svg>", svgStart) if svgStart >= 0 else -1
    if svgEnd < 0:
        return page
    parser = DiagramParser()
    parser.feed(page[svgStart:svgEnd])
    parser.close()
    nodes = parser.nodes
    edges = {edgeId: edge for edgeId, edge in parser.edges.items() if edge["from"] in nodes and edge["to"] in nodes}
    if not nodes:
        return page
    diagram = {"nodes": nodes, "edges": edges, "kinds": parser.kinds}
    adjacency = {"up": {}, "down": {}}
    for edge in edges.values():
        adjacency["down"].setdefault(edge["from"], []).append(edge["to"])
        adjacency["up"].setdefault(edge["to"], []).append(edge["from"])
    details = [_nodeDetails(nodeId, diagram, adjacency) for nodeId in nodes] + [_edgeDetails(edgeId, diagram) for edgeId in edges]
    svg = LINK_GROUP.sub(lambda match: _focusableLink(match, diagram), page[svgStart:svgEnd])
    svg = PLAIN_EDGE.sub(lambda match: f'<path {match.group(1)} tabindex="0"/>' if match.group(2) in edges else match.group(0), svg)
    style = '<style id="siterm-details">' + DETAILS_STYLE + "\n".join(rule for _, rule in details) + "\n</style>"
    panels = '<div id="siterm-details-panels">' + "".join(panel for panel, _ in details) + "</div>"
    page = page[:svgStart] + svg + page[svgEnd:]
    bodyEnd = page.rfind("</body>")
    page = page[:bodyEnd] + panels + page[bodyEnd:] if bodyEnd >= 0 else page + panels
    return page.replace("</head>", style + "</head>", 1)


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
    return addDetails(body.decode("utf-8", errors="replace")).replace("</head>", STATIC_STYLE + "</head>", 1).encode()


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
