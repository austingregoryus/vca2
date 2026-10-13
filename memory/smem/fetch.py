# SPDX-License-Identifier: MIT
"""Fetch a web page and turn it into markdown-ish text for ingest.

The API has no authentication by default and sits on a LAN. A "fetch this
URL" endpoint on such a server is a classic SSRF hole: anyone on the network
could make it request a router's admin page, the Proxmox API on :8006, or
the model servers on the private node network. So:

  - only http and https,
  - every address the hostname resolves to must be public, checked BEFORE
    connecting and again on every redirect hop (a public URL that redirects
    to 10.0.0.1 is the usual bypass),
  - bounded size and time.

SMEM_ALLOW_PRIVATE_URLS=1 turns the address check off, for deliberately
ingesting pages from your own LAN.

Known gap: DNS rebinding. The name is resolved once for the check and again
by urllib when it connects, so a hostile DNS server answering with a public
address and then a private one can slip past. Closing that means connecting
to the checked IP directly (with SNI/Host set by hand); not worth it for a
LAN tool whose API is already unauthenticated by default.
"""

import html.parser
import ipaddress
import os
import re
import socket
import urllib.parse
import urllib.request

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 20
ALLOW_PRIVATE = os.environ.get("SMEM_ALLOW_PRIVATE_URLS") == "1"


class FetchError(ValueError):
    pass


def _check_host(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise FetchError("only http and https URLs can be ingested")
    if not u.hostname:
        raise FetchError("URL has no host")
    if ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror as e:
        raise FetchError(f"cannot resolve {u.hostname}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise FetchError(f"{u.hostname} resolves to {ip}, a non-public address; "
                             "set SMEM_ALLOW_PRIVATE_URLS=1 to allow LAN pages")


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host(newurl)          # re-validate every hop
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Text(html.parser.HTMLParser):
    """HTML -> text, keeping headings as markdown so the chunker can use them."""
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header",
            "form", "button", "iframe", "template"}
    BLOCK = {"p", "div", "section", "article", "li", "tr", "br", "pre",
             "blockquote", "table", "ul", "ol", "dd", "dt", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif re.fullmatch(r"h[1-6]", tag) or tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)

    def text(self):
        t = "".join(self.out)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\n[ ]+", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def fetch_url(url):
    """-> (title, text). Raises FetchError with a human-readable reason."""
    _check_host(url)
    opener = urllib.request.build_opener(_Redirect)
    req = urllib.request.Request(url, headers={
        "User-Agent": "smem/0.2 (+local memory ingest)",
        "Accept": "text/html,text/plain,text/markdown;q=0.9,*/*;q=0.1"})
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "").lower()
            if not any(t in ctype for t in ("text/html", "text/plain", "markdown",
                                            "application/xhtml")):
                raise FetchError(f"unsupported content type {ctype or 'unknown'}; "
                                 "only web pages and plain text are ingested")
            raw = r.read(MAX_BYTES + 1)
            final = r.geturl()
    except FetchError:
        raise
    except Exception as e:
        raise FetchError(f"fetch failed: {e}")
    if len(raw) > MAX_BYTES:
        raise FetchError("page is larger than 5 MB")
    m = re.search(r"charset=([\w-]+)", ctype)
    body = raw.decode(m.group(1) if m else "utf-8", errors="replace")
    if "html" in ctype:
        p = _Text()
        p.feed(body)
        title, text = p.title.strip(), p.text()
    else:
        text = body.strip()
        # Markdown and plain text have no <title>; a leading "# Heading" is
        # the closest thing, and beats falling back to the hostname.
        h = re.search(r"^#{1,2}[ \t]+(.+?)[ \t#]*$", text[:4000], re.M)
        title = h.group(1).strip() if h else ""
    if len(text) < 200:
        raise FetchError("the page had almost no readable text "
                         "(it may need JavaScript to render)")
    return (title or urllib.parse.urlsplit(final).hostname or url), text
