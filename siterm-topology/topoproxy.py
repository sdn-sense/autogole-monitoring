#!/usr/bin/env python3
"""Serve SiteRM topology and geographic network pages to Grafana."""

# Runs as a sidecar of the Prometheus pod and reads the rm-configs checkout oidcgen keeps in a shared volume:
# each site directory names its Frontends, and oidcgen writes that directory's token to oidc-<dir>.token.
# Grafana reaches it only through its data source proxy, which requires a logged-in user and
# sandboxes the response without scripts.

import base64
import hmac
import html
import json
import math
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple

# SiteRM's own diagram builder and Archify renderer, copied into the image at a pinned siterm commit (see Dockerfile).
from SiteRMLibs.ArchifyTopology import ArchifyRenderError, ArchifyUnavailable, buildDiagram, portKey, renderDiagramHTML
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
GLOBAL_PATH = "/global-topology"
WORLD_PATH = "/world-map"
GLOBAL_NAME = "All SiteRM sites"
WORLD_NAME = "SiteRM world map"
# Expanded site lists longer than this are cut (each name is at most 128 characters, see REQUEST_PATH).
MAX_EXPANDED = 64
GLOBAL_PAGE_CACHE = 64
GLOBAL_WORKERS = int(os.environ.get("TOPO_GLOBAL_WORKERS", "8"))
GLOBAL_RENDER_TIMEOUT = int(os.environ.get("TOPO_GLOBAL_RENDER_TIMEOUT", "300"))
WORLD_MAP_FILE = os.environ.get("WORLD_MAP_SVG", os.path.join(os.path.dirname(__file__), "world-map.svg"))
WORLD_WIDTH = 1200
WORLD_HEIGHT = 600
WORLD_REFRESH = 300
WORLD_STALE_REFRESH = 60
WORLD_LOADING_REFRESH = 5
US_SITE = re.compile(r"(?:^|_)US(?:_|$)", re.IGNORECASE)
# isAlias URNs: urn:ogf:network:<domain>[:<year>]:<device>:<port>
URN_PREFIX = "urn:ogf:network"
NOTE_STYLE = (
    "position:fixed;top:8px;right:8px;z-index:40;max-width:min(560px,calc(100vw - 16px));padding:6px 10px;"
    "border:1px solid var(--panel-border);border-radius:8px;background:var(--toolbar-menu-bg, var(--bg));color:var(--text-muted);font-size:11px"
)
# Collapsed sites are links (a sandboxed page cannot run scripts, but it may navigate itself): a transparent box over the node.
EXPAND_STYLE = """
svg a.st-expand rect { fill: transparent; stroke: transparent; stroke-width: 2.5; cursor: pointer; }
svg a.st-expand:hover rect, svg a.st-expand:focus rect { stroke: var(--frontend-stroke); outline: none; }
.st-note a { color: var(--frontend-stroke); }
"""
RESPONSE_HEADERS = {
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=60",
}
# The combined page's site nodes are links that reload the frame; a sandbox with an opaque origin blocked that in Chromium.
# allow-same-origin without allow-scripts still runs no script, so the page can do no more than follow its own links.
GLOBAL_CSP = "sandbox allow-same-origin"
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


class SiteTarget(NamedTuple):
    """One monitored Frontend and the geographic metadata published in its config."""

    base: str
    tokenFile: str
    domain: str
    latitude: float | None
    longitude: float | None


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


def _number(value, minimum, maximum):
    """Return a finite float in the inclusive range, or None for invalid configuration."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and minimum <= value <= maximum else None


def _siteCoordinates(site, config):
    """Return a validated (latitude, longitude), correcting legacy unsigned US longitudes."""
    latitude = _number(config.get("latitude"), -90, 90)
    longitude = _number(config.get("longitude"), -180, 180)
    if latitude is None or longitude is None:
        return None, None
    # Several long-lived US configs published west longitude as a positive number. Their SENSE model
    # contains the same value, so fix it at the display boundary while leaving the source untouched.
    if longitude > 0 and US_SITE.search(site):
        longitude = -longitude
    return latitude, longitude


def loadSites(repoDir, tokenDir):
    """Return a SiteTarget for every enabled Frontend in an rm-configs checkout.

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
                siteConfig = conf.get(site) if isinstance(conf.get(site), dict) else {}
                domain = siteConfig.get("domain")
                latitude, longitude = _siteCoordinates(site, siteConfig)
                sites.setdefault(site, SiteTarget(base, tokenFile, domain if isinstance(domain, str) else "", latitude, longitude))
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

    def all(self):
        """Return {site: SiteTarget} for every monitored site."""
        with self.lock:
            if time.time() >= self.nextScan:
                try:
                    self.sites = loadSites(self.repoDir, self.tokenDir)
                except OSError as ex:
                    print(f"Keeping previous site list, cannot read {self.repoDir}: {ex}", flush=True)
                # Until oidcgen has cloned the repo, look again soon.
                self.nextScan = time.time() + (RESCAN if self.sites else 10)
            return dict(self.sites)

    def get(self, site):
        """Return the SiteTarget for a site, or None."""
        return self.all().get(site)


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


def _fetch(base, site, tokenFile, resource, accept):
    """GET /api/<site>/<resource> from a site's Frontend with its oidcgen token and return the body (at most MAX_BYTES)."""
    with open(tokenFile, "r", encoding="utf-8") as fd:
        token = fd.read().strip()
    url = f"{base}/api/{urllib.parse.quote(site)}/{resource}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": accept})
    with OPENER.open(req, timeout=TIMEOUT) as resp:
        body = resp.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError(f"{resource} response is larger than 16MB")
    return body


def _staticPage(page):
    """Add the details panels and hide the controls that need scripts."""
    return addDetails(page).replace("</head>", STATIC_STYLE + "</head>", 1)


def fetchTopology(base, site, tokenFile, pathId):
    """Fetch the Archify topology page of one site from its Frontend."""
    # Graph only: the legend, health and link cards stay on the SiteRM web UI.
    query = {"view": "graph", **({"path": pathId} if pathId else {})}
    body = _fetch(base, site, tokenFile, f"topo/archify?{urllib.parse.urlencode(query)}", "text/html")
    return _staticPage(body.decode("utf-8", errors="replace")).encode()


def fetchTopologyJson(base, site, tokenFile):
    """Fetch the topology.json content of one site from its Frontend."""
    topology = json.loads(_fetch(base, site, tokenFile, "topo/gettopology", "application/json"))
    if not isinstance(topology, dict):
        raise ValueError("topology is not a JSON object")
    return topology


def _aliasParts(alias):
    """Return (domain, device, port) of an isAlias URN, or None if it is not one."""
    parts = str(alias).split(":")
    if len(parts) < 6 or ":".join(parts[:3]) != URN_PREFIX or not all(parts[i] for i in (3, -2, -1)):
        return None
    return parts[3], parts[-2], parts[-1]


def _siteDevices(site, topology, devices):
    """Add one site's devices to devices as <site>:<device> and return its cloud entries [(alias, links)]; malformed entries are dropped."""
    clouds = []
    for name, entry in topology.items():
        info = entry.get("DeviceInfo") if isinstance(entry, dict) else None
        topo = entry.get("topo") if isinstance(entry, dict) else None
        if not isinstance(info, dict) or not isinstance(topo, dict):
            continue
        links = {str(port): {**link, "device": f"{site}:{link['device']}"} for port, link in topo.items() if isinstance(link, dict) and isinstance(link.get("device"), str)}
        if info.get("type") == "cloud":
            clouds.append((str(info.get("name", "")), links))
        elif info.get("type") in ("switch", "server", "lan"):
            devices[f"{site}:{name}"] = {"DeviceInfo": {**info, "name": f"{site}:{name}"}, "topo": links}
    return clouds


def mergeTopologies(topologies, domains):
    """Merge per-site topology.json content into one topology for buildDiagram.

    Devices become <site>:<device>. A WAN alias to a device of another fetched site becomes a direct link (one per pair even when
    both sites report it); any other alias joins one cloud per remote domain, or "unknown" when it is not an isAlias URN.
    """
    merged, clouds = {}, []
    for site in sorted(topologies):
        clouds.extend(_siteDevices(site, topologies[site], merged))
    siteByDomain = {domain: site for site, domain in sorted(domains.items()) if domain and site in topologies}
    seen = set()
    # When both sites report one link, keep the report that lists port-channel members.
    reports = sorted(((alias, port, link) for alias, links in clouds for port, link in links.items()), key=lambda report: not report[2].get("members"))
    for alias, remotePort, link in reports:
        parts = _aliasParts(alias)
        peer = f"{siteByDomain[parts[0]]}:{parts[1]}" if parts and parts[0] in siteByDomain else None
        if peer in merged:
            pair = frozenset(((peer, portKey(remotePort)), (link["device"], portKey(link.get("localport") or ""))))
            if pair in seen:
                continue
            if remotePort not in merged[peer]["topo"]:
                seen.add(pair)
                # WAN entry shape (dict port, localport, members) on the remote device, so port-channel members still expand.
                merged[peer]["topo"][remotePort] = link
                continue
        _addToCloud(merged, parts, f"{parts[1]}:{remotePort}" if parts else alias or "unknown", link)
    return merged


def _addToCloud(merged, parts, port, link):
    """Add one WAN link to the cloud of its remote domain ("unknown" without a parsed alias); port names the remote end."""
    cloud = merged.setdefault(parts[0] if parts else "unknown", {"DeviceInfo": {"type": "cloud", "name": ""}, "topo": {}})
    key, count = port, 1
    while key in cloud["topo"]:
        count += 1
        key = f"{port} #{count}"
    cloud["topo"][key] = link


def _siteOf(name, entry):
    """Return the site of a merged device (<site>:<device>), or None for a cloud."""
    return None if entry["DeviceInfo"]["type"] == "cloud" else name.split(":", 1)[0]


def _prefixed(value, prefix):
    """Return "<prefix>:<value>", or value when it is empty."""
    return f"{prefix}:{value}" if value else value


def _collapseLink(link, siteNodes):
    """Point a link at its peer's site node when that site is collapsed, keeping the peer device name in the peer's port names."""
    device = link["device"]
    if device not in siteNodes:
        return link
    short = device.split(":", 1)[1]
    link = {**link, "device": siteNodes[device]}
    if isinstance(link.get("port"), dict):
        # WAN entry shape: the peer's ports are localport and the port-channel members.
        link["localport"] = _prefixed(str(link.get("localport") or ""), short)
        if isinstance(link.get("members"), dict):
            link["members"] = {f"{short}:{member}": speed for member, speed in link["members"].items()}
        if isinstance(link.get("membersdown"), list):
            link["membersdown"] = [f"{short}:{member}" for member in link["membersdown"]]
    else:
        link["port"] = _prefixed(str(link.get("port") or ""), short)
        link["remoteportchannel"] = _prefixed(str(link.get("remoteportchannel") or ""), short)
    return link


def collapseSites(merged, sites, expanded):
    """Return the merged topology with every site not in expanded drawn as one site node.

    Links between devices of one collapsed site become self links, which buildDiagram drops; port names keep the device name.
    """
    siteNodes = {name: _siteOf(name, entry) for name, entry in merged.items() if _siteOf(name, entry) not in (None, *expanded)}
    out = {site: {"DeviceInfo": {"type": "site", "name": site}, "topo": {}} for site in sites if site not in expanded}
    for name, entry in merged.items():
        links = {port: _collapseLink(link, siteNodes) for port, link in entry["topo"].items()}
        if name not in siteNodes:
            out[name] = {**entry, "topo": links}
            continue
        short = name.split(":", 1)[1]
        for port, link in links.items():
            if not isinstance(link.get("port"), dict) and link.get("portchannel"):
                link = {**link, "portchannel": _prefixed(str(link["portchannel"]), short)}
            out[siteNodes[name]]["topo"][f"{short}:{port}"] = link
    return out


def _expandQuery(sites):
    """Return the relative URL of the combined page with these sites expanded (all collapsed when empty)."""
    return "?" + urllib.parse.urlencode({"expand": ",".join(sorted(sites))}) if sites else "?"


def _expandLinks(page, diagram, expanded):
    """Make every collapsed site node a link that expands it, keeping the sites already expanded."""
    boxes = []
    for comp in diagram["ir"]["components"]:
        if comp["type"] != "frontend":
            continue
        (posX, posY), (width, height) = comp["pos"], comp["size"]
        href = html.escape(_expandQuery(set(expanded) | {comp["label"]}))
        label = html.escape(f"Expand {comp['label']}")
        boxes.append(f'<a class="st-expand" href="{href}" aria-label="{label}"><title>{label}</title><rect x="{posX:g}" y="{posY:g}" width="{width:g}" height="{height:g}" rx="8"/></a>')
    svgEnd = page.find("</svg>", page.find("<svg viewBox"))
    if not boxes or svgEnd < 0:
        return page
    return page[:svgEnd] + "".join(boxes) + page[svgEnd:]


def _note(expanded, full, failures, total):
    """Return the top-right note: how to expand, links to collapse expanded sites, and the sites that could not be fetched."""
    parts = []
    if full:
        parts.append("All sites expanded.")
    elif expanded:
        links = [f'{html.escape(site)} (<a href="{html.escape(_expandQuery(set(expanded) - {site}))}">collapse</a>)' for site in sorted(expanded)]
        parts.append(f'Expanded: {", ".join(links)}. <a href="?">Collapse all</a>')
    else:
        parts.append("Click a site to expand it.")
    if failures:
        missing = "; ".join(f"{site}: {reason}" for site, reason in sorted(failures.items()))
        parts.append(f"{total - len(failures)} of {total} sites shown. Missing: {html.escape(missing)}")
    return f'<div class="st-note" style="{NOTE_STYLE}">{"<br>".join(parts)}</div>'


def _portName(value):
    """Return a stable text representation of a topology port for edge deduplication."""
    try:
        value = portKey(value)
    except (KeyError, TypeError, ValueError):
        pass
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value or "")


def _edgeEndpoint(site, device, port, cloud=False):
    """Return a canonical physical endpoint, matching a cloud alias to its eventual merged device name."""
    endpoint = f"{site}:{_portName(port)}" if cloud else f"{device}:{_portName(port)}"
    # _addToCloud adds this suffix when duplicate one-sided reports share a remote port. They describe
    # the same physical endpoint and must not inflate the connection count.
    return re.sub(r" #\d+$", "", endpoint) if cloud else endpoint


def siteConnections(merged, siteByDomain=None):
    """Return deduplicated direct cross-site connections from a merged topology.

    A cloud whose name is a configured site domain represents that site when its own topology fetch failed.
    External clouds are excluded. Symmetric and duplicate one-sided reports share a canonical endpoint key.
    """
    siteByDomain = siteByDomain or {}
    connections = {}
    for device, entry in sorted(merged.items()):
        sourceSite = _siteOf(device, entry)
        sourceIsCloud = sourceSite is None
        if sourceIsCloud:
            sourceSite = siteByDomain.get(device)
        if not sourceSite:
            continue
        for ownPort, link in sorted(entry.get("topo", {}).items(), key=lambda item: str(item[0])):
            if not isinstance(link, dict) or not isinstance(link.get("device"), str):
                continue
            peer = link["device"]
            peerEntry = merged.get(peer)
            if not isinstance(peerEntry, dict):
                continue
            peerSite = _siteOf(peer, peerEntry)
            if peerSite is None or peerSite == sourceSite:
                continue
            pair = tuple(sorted((sourceSite, peerSite)))
            remotePort = link.get("localport") or link.get("port") or link.get("remoteportchannel") or ""
            endpoints = tuple(sorted((_edgeEndpoint(sourceSite, device, ownPort, sourceIsCloud), _edgeEndpoint(peerSite, peer, remotePort))))
            connections.setdefault(pair, set()).add(endpoints)
    return [
        {"source": source, "target": target, "links": len(connections[(source, target)])}
        for source, target in sorted(connections)
    ]


def _project(latitude, longitude):
    """Project WGS84 coordinates onto the map's equirectangular viewBox."""
    return (longitude + 180) * WORLD_WIDTH / 360, (90 - latitude) * WORLD_HEIGHT / 180


def mapPositions(locations):
    """Project sites and deterministically spread markers that share coordinates."""
    groups = {}
    for site, (latitude, longitude) in sorted(locations.items()):
        groups.setdefault((round(latitude, 7), round(longitude, 7)), []).append(site)
    positions = {}
    for (latitude, longitude), sites in sorted(groups.items()):
        centerX, centerY = _project(latitude, longitude)
        radius = min(24, 7 + len(sites) * 1.5) if len(sites) > 1 else 0
        for index, site in enumerate(sorted(sites)):
            angle = -math.pi / 2 + index * 2 * math.pi / len(sites)
            posX = centerX + radius * math.cos(angle)
            posY = centerY + radius * math.sin(angle)
            positions[site] = (min(WORLD_WIDTH - 8, max(8, posX)), min(WORLD_HEIGHT - 8, max(8, posY)))
    return positions


def _arcPaths(start, end):
    """Return curved SVG paths between two points, wrapping cleanly across the antimeridian."""
    startX, startY = start
    endX, endY = end
    if abs(endX - startX) > WORLD_WIDTH / 2:
        endX += -WORLD_WIDTH if endX > startX else WORLD_WIDTH
    deltaX, deltaY = endX - startX, endY - startY
    distance = max(1, math.hypot(deltaX, deltaY))
    bend = min(58, max(16, distance * 0.13))
    controlX = (startX + endX) / 2 - deltaY * bend / distance
    controlY = (startY + endY) / 2 + deltaX * bend / distance
    paths = []
    for shift in (-WORLD_WIDTH, 0, WORLD_WIDTH):
        shifted = (startX + shift, controlX + shift, endX + shift)
        if max(shifted) >= 0 and min(shifted) <= WORLD_WIDTH:
            paths.append(f"M{shifted[0]:.2f},{startY:.2f} Q{shifted[1]:.2f},{controlY:.2f} {shifted[2]:.2f},{endY:.2f}")
    return paths


def _worldGeometry():
    """Return the trusted bundled map geometry, or a small built-in fallback when the asset is unavailable."""
    try:
        with open(WORLD_MAP_FILE, "r", encoding="utf-8") as fd:
            source = fd.read()
        start, end = source.find("<svg"), source.rfind("</svg>")
        body = source[source.find(">", start) + 1 : end] if start >= 0 and end >= 0 else ""
        if not body or "<script" in body.lower():
            raise ValueError("invalid world map SVG")
        return re.sub(r"<title>.*?</title>", "", body, count=1, flags=re.DOTALL)
    except (OSError, ValueError) as ex:
        print(f"World map asset unavailable: {type(ex).__name__}: {ex}", flush=True)
        meridians = "".join(f'<path d="M{x},0V{WORLD_HEIGHT}"/>' for x in range(0, WORLD_WIDTH + 1, 200))
        parallels = "".join(f'<path d="M0,{y}H{WORLD_WIDTH}"/>' for y in range(0, WORLD_HEIGHT + 1, 100))
        return f'<rect x="0" y="0" width="{WORLD_WIDTH}" height="{WORLD_HEIGHT}" rx="18"/>{meridians}{parallels}'


WORLD_STYLE = """
:root { color-scheme: dark; --bg:#070b13; --panel:#101826e8; --border:#263751; --text:#eef5ff;
  --muted:#93a5bc; --land:#17263a; --coast:#526b89; --live:#49d7a1; --down:#f4a261; --link:#55b7ff; }
* { box-sizing:border-box; }
html, body { width:100%; min-height:100%; margin:0; background:radial-gradient(circle at 50% 0,#12243c 0,#070b13 58%); color:var(--text);
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
body { min-height:100vh; display:flex; flex-direction:column; overflow-x:hidden; }
.topbar { display:flex; align-items:center; gap:20px; padding:16px 22px 13px; border-bottom:1px solid #263751aa;
  background:linear-gradient(180deg,#101a2aee,#0a111ddd); backdrop-filter:blur(12px); }
.brand { min-width:220px; }
.eyebrow { color:#6cbfff; font-size:10px; font-weight:750; letter-spacing:.18em; text-transform:uppercase; }
h1 { margin:3px 0 0; font-size:20px; line-height:1.1; letter-spacing:-.02em; }
.stats { display:flex; flex:1; flex-wrap:wrap; gap:8px; }
.stat { min-width:88px; padding:6px 11px; border:1px solid var(--border); border-radius:9px; background:#0d1725aa; }
.stat b { display:block; color:var(--text); font-size:15px; line-height:1.1; }
.stat span { color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.08em; }
.graph-link { flex:none; padding:8px 12px; border:1px solid #438bc0; border-radius:8px; color:#a9ddff; text-decoration:none; font-size:12px; font-weight:650; }
.graph-link:hover, .graph-link:focus-visible { color:white; background:#173552; outline:2px solid #67c3ff; outline-offset:2px; }
main { flex:1; display:flex; min-height:0; padding:14px; }
.map-shell { position:relative; flex:1; min-height:420px; overflow:hidden; border:1px solid var(--border); border-radius:14px;
  background:linear-gradient(180deg,#091425,#070d17); box-shadow:0 18px 50px #0008,inset 0 1px #ffffff0b; }
.world { display:block; width:100%; height:100%; min-height:420px; }
.ocean-grid path { fill:none; stroke:#6a86a9; stroke-width:.45; opacity:.16; vector-effect:non-scaling-stroke; }
.land { color:var(--coast); fill:var(--land); }
.land path { fill:var(--land); stroke:var(--coast); }
.connection { fill:none; stroke:var(--link); stroke-linecap:round; opacity:.62; vector-effect:non-scaling-stroke;
  filter:drop-shadow(0 0 3px #2aa7ff80); transition:opacity .15s,stroke-width .15s; }
.edge:hover .connection, .edge:focus .connection { opacity:1; stroke:#a8dcff; stroke-width:3.2 !important; }
.edge:focus { outline:none; }
.site { color:var(--live); text-decoration:none; }
.site.offline { color:var(--down); }
.site .halo { fill:currentColor; opacity:.18; }
.site .dot { fill:#09111d; stroke:currentColor; stroke-width:3; vector-effect:non-scaling-stroke; filter:drop-shadow(0 0 5px currentColor); }
.site text { fill:#e8f2ff; font-size:11px; font-weight:700; text-anchor:middle; paint-order:stroke; stroke:#07101c; stroke-width:3px;
  stroke-linejoin:round; pointer-events:none; transition:opacity .15s; }
.site.clustered text { opacity:0; }
.site.clustered:hover text, .site.clustered:focus text { opacity:1; }
.site:hover .halo, .site:focus .halo { opacity:.42; }
.site:hover .dot, .site:focus .dot { fill:currentColor; stroke:white; }
.site:focus { outline:none; }
.legend, .status { position:absolute; z-index:3; bottom:14px; padding:8px 11px; border:1px solid var(--border); border-radius:9px;
  background:var(--panel); color:var(--muted); font-size:11px; box-shadow:0 8px 24px #0007; backdrop-filter:blur(8px); }
.legend { left:14px; display:flex; gap:14px; }
.legend span { display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }
.swatch { display:inline-block; flex:none; width:9px; height:9px; border-radius:50%; background:var(--live); box-shadow:0 0 5px var(--live); }
.swatch.down { background:var(--down); box-shadow:0 0 5px var(--down); }
.swatch.link { width:18px; height:2px; border-radius:2px; background:var(--link); box-shadow:none; }
.status { right:14px; max-width:min(420px,calc(100% - 28px)); text-align:right; }
.status strong { color:#cfe8ff; }
@media (max-width:760px) { .topbar { align-items:flex-start; flex-wrap:wrap; gap:10px; padding:12px 14px; } .brand { min-width:0; flex:1; }
  h1 { font-size:17px; } .stats { order:3; flex-basis:100%; } .stat { min-width:72px; flex:1; } main { padding:8px; }
  .map-shell,.world { min-height:360px; } .status { top:10px; right:10px; bottom:auto; } .legend { left:10px; bottom:10px; } }
"""


def renderWorldMap(data):
    """Render a responsive, script-free SVG world map from cached GlobalTopology data."""
    locations = {}
    for site, location in data.get("locations", {}).items():
        if not isinstance(site, str) or not isinstance(location, (list, tuple)) or len(location) != 2:
            continue
        latitude, longitude = _number(location[0], -90, 90), _number(location[1], -180, 180)
        if latitude is not None and longitude is not None:
            locations[site] = (latitude, longitude)
    positions = mapPositions(locations)
    locationCounts = {}
    for latitude, longitude in locations.values():
        key = (round(latitude, 7), round(longitude, 7))
        locationCounts[key] = locationCounts.get(key, 0) + 1
    online = set(data.get("sites", ()))
    failures = data.get("failures", {})
    connections = [
        edge
        for edge in siteConnections(data.get("merged", {}), data.get("siteByDomain", {}))
        if edge["source"] in positions and edge["target"] in positions
    ]

    edgeMarkup = []
    for index, edge in enumerate(connections):
        source, target, count = edge["source"], edge["target"], edge["links"]
        title = html.escape(f"{source} ↔ {target} · {_plural(count, 'WAN link')}")
        width = min(3, 1.25 + math.log2(count + 1) * 0.55)
        paths = "".join(f'<path class="connection" style="stroke-width:{width:.2f}" d="{path}"/>' for path in _arcPaths(positions[source], positions[target]))
        edgeMarkup.append(f'<g class="edge" tabindex="0" role="img" aria-label="{title}" data-edge="{index}"><title>{title}</title>{paths}</g>')

    siteMarkup = []
    for index, site in enumerate(sorted(positions)):
        latitude, longitude = locations[site]
        isOnline = site in online
        state = "Topology live" if isOnline else failures.get(site, "Topology unavailable")
        title = html.escape(f"{site} · {latitude:.4f}, {longitude:.4f} · {state}")
        label = html.escape(site)
        href = "topology/" + urllib.parse.quote(site, safe="")
        posX, posY = positions[site]
        clustered = locationCounts[(round(latitude, 7), round(longitude, 7))] > 1
        css = ("site" if isOnline else "site offline") + (" clustered" if clustered else "")
        siteMarkup.append(
            f'<a class="{css}" href="{href}" aria-label="{title}" data-site="{index}"><title>{title}</title>'
            f'<circle class="halo" cx="{posX:.2f}" cy="{posY:.2f}" r="11"/><circle class="dot" cx="{posX:.2f}" cy="{posY:.2f}" r="5"/>'
            f'<text x="{posX:.2f}" y="{posY - 12:.2f}">{label}</text></a>'
        )

    total = max(len(locations), int(data.get("total", len(locations))))
    onlineCount = len(online)
    missing = max(0, total - len(locations))
    wanLinks = sum(edge["links"] for edge in connections)
    updated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("updated", time.time())))
    locationNote = f" · {missing} without coordinates" if missing else ""
    grid = "".join(f'<path d="M{x},0V{WORLD_HEIGHT}"/>' for x in range(0, WORLD_WIDTH + 1, 200))
    grid += "".join(f'<path d="M0,{y}H{WORLD_WIDTH}"/>' for y in range(0, WORLD_HEIGHT + 1, 100))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{WORLD_REFRESH}"><title>{WORLD_NAME}</title><style>{WORLD_STYLE}</style></head><body>
<header class="topbar"><div class="brand"><div class="eyebrow">SENSE / SiteRM</div><h1>Global network topology</h1></div>
<div class="stats"><div class="stat"><b>{total}</b><span>Monitored sites</span></div><div class="stat"><b>{onlineCount}</b><span>Topologies live</span></div>
<div class="stat"><b>{len(connections)}</b><span>Site connections</span></div><div class="stat"><b>{wanLinks}</b><span>Direct WAN links</span></div></div>
<a class="graph-link" href="global-topology">Topology graph&nbsp; →</a></header>
<main><section class="map-shell" aria-label="SiteRM world map"><svg class="world" viewBox="0 0 {WORLD_WIDTH} {WORLD_HEIGHT}" preserveAspectRatio="xMidYMid meet" role="img" aria-labelledby="world-title world-desc">
<title id="world-title">SiteRM sites and direct WAN connections</title><desc id="world-desc">Select a site marker to open its topology.</desc>
<g class="ocean-grid">{grid}</g><g class="land">{_worldGeometry()}</g><g class="connections">{"".join(edgeMarkup)}</g><g class="sites">{"".join(siteMarkup)}</g></svg>
<div class="legend" aria-label="Map legend"><span><i class="swatch"></i>Live</span><span><i class="swatch down"></i>Unavailable</span><span><i class="swatch link"></i>Direct WAN</span></div>
<div class="status"><strong>Live configuration</strong> · updated {updated}{locationNote}</div></section></main></body></html>'''.encode()


def errorPage(site, message, refresh=None):
    """Return a small page that tells the dashboard viewer why there is no diagram."""
    text = html.escape(f"Topology for {site} is not available: {message}")
    style = "margin:16px;font-family:sans-serif;color:#9aa3b2;background:#181b1f"
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return f'<!doctype html><html><head>{meta}</head><body style="{style}"><p>{text}</p></body></html>'.encode()


def _staleWorldPage(result):
    """Shorten a cached map's reload interval while its replacement is being fetched."""
    status, page = result
    normal = f'<meta http-equiv="refresh" content="{WORLD_REFRESH}">'.encode()
    stale = f'<meta http-equiv="refresh" content="{WORLD_STALE_REFRESH}">'.encode()
    return status, page.replace(normal, stale, 1)


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
        if not os.path.isfile(target.tokenFile):
            return 503, errorPage(site, "oidcgen has no token for this site yet (is its Frontend reachable?)")
        try:
            page = fetchTopology(target.base, site, target.tokenFile, pathId)
        except urllib.error.HTTPError as ex:
            status, ttl, page = 502, ERROR_TTL, errorPage(site, _frontendError(ex))
        except (OSError, ValueError) as ex:
            status, ttl, page = 502, ERROR_TTL, errorPage(site, f"could not reach the Frontend ({type(ex).__name__})")
        with self.lock:
            self.entries = {k: v for k, v in self.entries.items() if v[0] > time.time()}
            self.entries[key] = (time.time() + ttl, status, page)
        return status, page


def _siteError(ex):
    """Describe why one site's topology.json could not be fetched."""
    if isinstance(ex, urllib.error.HTTPError):
        return _frontendError(ex)
    return f"could not reach the Frontend ({type(ex).__name__})"


class GlobalTopology:
    """Cached live data shared by the combined graph and geographic world map."""

    def __init__(self, sites):
        self.sites = sites
        self.data = None  # {"expires", "merged", "sites", "failures", "total"} or {"expires", "error"}
        self.pages = {}
        self.worldPage = None
        self.building = False
        self.lock = threading.Lock()

    def _startRefresh(self):
        """Start at most one background refresh. Caller must hold self.lock."""
        if (self.data is None or self.data["expires"] <= time.time()) and not self.building:
            self.building = True
            threading.Thread(target=self._refresh, daemon=True).start()

    def prewarm(self):
        """Begin the initial refresh without blocking service startup."""
        with self.lock:
            self._startRefresh()

    def get(self, expand, full):
        """Return (HTTP status, page) with the given sites expanded (all of them when full), or 503 while the first fetch runs."""
        with self.lock:
            self._startRefresh()
            data = self.data
            if data is None:
                return 503, errorPage(GLOBAL_NAME, "the combined topology is being built, reload in a minute")
            if "error" in data:
                return 502, errorPage(GLOBAL_NAME, data["error"])
            if not data["sites"]:
                return 502, errorPage(GLOBAL_NAME, "no site Frontend returned its topology")
            expanded = tuple(sorted(set(expand) & set(data["sites"])))
            key = (expanded, full)
            if key in self.pages:
                return self.pages[key]
        result = self._render(data, expanded, full)
        with self.lock:
            if self.data is data:
                if len(self.pages) >= GLOBAL_PAGE_CACHE:
                    self.pages.clear()
                self.pages[key] = result
        return result

    def getWorld(self):
        """Return the world map, using the same refresh and merged topology as the combined graph."""
        with self.lock:
            expired = self.data is not None and self.data["expires"] <= time.time()
            self._startRefresh()
            data = self.data
            if data is None:
                return 503, errorPage(WORLD_NAME, "the site data is being built, reload in a moment", refresh=WORLD_LOADING_REFRESH)
            if "error" in data:
                return 502, errorPage(WORLD_NAME, data["error"])
            stale = expired and self.building
            if self.worldPage is not None:
                return _staleWorldPage(self.worldPage) if stale else self.worldPage
        result = 200, renderWorldMap(data)
        with self.lock:
            if self.data is data:
                self.worldPage = result
        return _staleWorldPage(result) if stale else result

    def _refresh(self):
        """Fetch and merge all sites; a failed fetch keeps the previous topology and retries after ERROR_TTL."""
        try:
            data = self.build()
        except Exception as ex:  # pylint: disable=broad-except
            # A daemon thread must never die silently with building stuck at True.
            print(f"Combined topology build failed: {type(ex).__name__}: {ex}", flush=True)
            data = {"expires": time.time() + ERROR_TTL, "error": "the combined topology could not be built"}
        with self.lock:
            if "error" in data and self.data is not None and "error" not in self.data:
                data = {**self.data, "expires": data["expires"]}
            else:
                self.pages = {}
                self.worldPage = None
            self.data = data
            self.building = False

    @staticmethod
    def _fetchAll(targets):
        """Fetch topology.json of every site in parallel; returns ({site: topology}, {site: why it is missing})."""
        topologies, failures = {}, {}
        with ThreadPoolExecutor(max_workers=GLOBAL_WORKERS) as pool:
            futures = {}
            for site, target in sorted(targets.items()):
                if os.path.isfile(target.tokenFile):
                    futures[site] = pool.submit(fetchTopologyJson, target.base, site, target.tokenFile)
                else:
                    failures[site] = "oidcgen has no token for this site yet"
            for site, future in futures.items():
                try:
                    topologies[site] = future.result()
                except (OSError, ValueError) as ex:
                    failures[site] = _siteError(ex)
        return topologies, failures

    def build(self):
        """Fetch every site's topology.json and merge them; returns the data get() renders views from."""
        targets = self.sites.all()
        topologies, failures = self._fetchAll(targets)
        merged = mergeTopologies(topologies, {site: target.domain for site, target in targets.items()}) if topologies else {}
        siteByDomain = {}
        for site, target in sorted(targets.items()):
            if target.domain:
                siteByDomain.setdefault(target.domain, site)
        locations = {
            site: (target.latitude, target.longitude)
            for site, target in targets.items()
            if target.latitude is not None and target.longitude is not None
        }
        return {
            "expires": time.time() + (CACHE_TTL if topologies else ERROR_TTL),
            "updated": time.time(),
            "merged": merged,
            "siteByDomain": siteByDomain,
            "sites": sorted(topologies),
            "locations": locations,
            "failures": failures,
            "total": len(targets),
        }

    @staticmethod
    def _render(data, expanded, full):
        """Render one view: collapsed sites (except expanded) as clickable site nodes, or every device when full."""
        topology = data["merged"] if full else collapseSites(data["merged"], data["sites"], expanded)
        diagram = buildDiagram(topology, GLOBAL_NAME, cards=False)
        try:
            page = _staticPage(renderDiagramHTML(diagram, timeout=GLOBAL_RENDER_TIMEOUT))
        except (ArchifyUnavailable, ArchifyRenderError) as ex:
            print(f"Combined topology render failed: {ex}", flush=True)
            return 502, errorPage(GLOBAL_NAME, f"the diagram could not be rendered ({type(ex).__name__})")
        page = _expandLinks(page, diagram, expanded).replace("</head>", f"<style>{EXPAND_STYLE}</style></head>", 1)
        note = _note(expanded, full, data["failures"], data["total"])
        bodyEnd = page.rfind("</body>")
        return 200, (page[:bodyEnd] + note + page[bodyEnd:] if bodyEnd >= 0 else page + note).encode()


def authorized(header):
    """Return True if the Authorization header has the configured basic auth credentials (or none are set)."""
    if not AUTH_PASSWORD:
        return True
    expected = "Basic " + base64.b64encode(f"{AUTH_USER}:{AUTH_PASSWORD}".encode()).decode("ascii")
    return hmac.compare_digest((header or "").encode(), expected.encode())


class Handler(BaseHTTPRequestHandler):
    """Serve per-site topology, the combined graph, the world map and health checks."""

    cache = None
    globalTopology = None

    def _send(self, status, body, contentType="text/html; charset=utf-8", csp=None):
        self.send_response(status)
        self.send_header("Content-Type", contentType)
        for key, val in {**RESPONSE_HEADERS, **({"Content-Security-Policy": csp} if csp else {})}.items():
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
        if url.path == GLOBAL_PATH:
            query = urllib.parse.parse_qs(url.query)
            expand = [site for site in query.get("expand", [""])[0].split(",") if site][:MAX_EXPANDED]
            self._send(*self.globalTopology.get(expand, query.get("view", [""])[0] == "full"), csp=GLOBAL_CSP)
            return
        if url.path == WORLD_PATH:
            self._send(*self.globalTopology.getWorld(), csp=GLOBAL_CSP)
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
    sites = SiteMap(REPO_DIR, TOKEN_DIR)
    Handler.cache = TopologyCache(sites)
    Handler.globalTopology = GlobalTopology(sites)
    Handler.globalTopology.prewarm()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
