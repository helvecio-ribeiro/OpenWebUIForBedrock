from __future__ import annotations

import html
import http.client
import ipaddress
import re
import socket
import ssl
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup, NavigableString, Tag


SUPPORTED_CONTENT_TYPES = {
    "application/xhtml+xml",
    "text/html",
    "text/plain",
}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain"}
DROP_TAGS = {
    "aside",
    "canvas",
    "dialog",
    "footer",
    "form",
    "iframe",
    "nav",
    "noscript",
    "script",
    "style",
    "svg",
    "template",
}


class WebResearchError(ValueError):
    """A safe, user-facing web retrieval error."""


@dataclass(frozen=True)
class Limits:
    max_response_bytes: int = 8 * 1024 * 1024
    max_output_characters: int = 50_000
    max_crawl_pages: int = 20
    max_crawl_depth: int = 2
    max_crawl_characters: int = 120_000
    timeout_seconds: float = 15.0
    max_redirects: int = 5
    crawl_delay_seconds: float = 0.25


@dataclass(frozen=True)
class RawResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    truncated: bool = False


Resolver = Callable[..., list[tuple]]
RequestOnce = Callable[[str, str, int, str, str, float, int], RawResponse]


def normalize_url(url: str) -> str:
    """Normalize and validate an absolute public HTTP(S) URL without resolving it."""
    if not isinstance(url, str) or not url.strip():
        raise WebResearchError("url is required")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise WebResearchError(f"invalid URL: {exc}") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise WebResearchError("only http and https URLs are allowed")
    if not parsed.hostname:
        raise WebResearchError("URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise WebResearchError("URLs containing credentials are not allowed")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
        raise WebResearchError("local hostnames are not allowed")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise WebResearchError("hostname is not valid IDNA") from exc
    if port is not None and not 1 <= port <= 65535:
        raise WebResearchError("URL port is outside the valid range")
    default_port = 443 if scheme == "https" else 80
    host_for_netloc = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    netloc = host_for_netloc if port in {None, default_port} else f"{host_for_netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global


def resolve_public_addresses(
    hostname: str,
    port: int,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> list[str]:
    """Resolve a hostname and reject the destination unless every answer is public."""
    try:
        records = resolver(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise WebResearchError(f"could not resolve destination hostname: {exc}") from exc
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise WebResearchError("destination hostname returned no addresses")
    blocked = [address for address in addresses if not _is_public_address(address)]
    if blocked:
        raise WebResearchError("destination resolves to a non-public network address")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._address, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self.sock = raw_socket
            self._tunnel()
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


def _request_once(
    scheme: str,
    hostname: str,
    port: int,
    address: str,
    target: str,
    timeout: float,
    max_bytes: int,
    *,
    user_agent: str = "LambdaWebUI-WebResearch/0.1",
) -> RawResponse:
    if scheme == "https":
        connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
            hostname, address, port, timeout
        )
    else:
        connection = http.client.HTTPConnection(address, port=port, timeout=timeout)
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if scheme == "https" else 80
    if port != default_port:
        host_header = f"{host_header}:{port}"
    try:
        connection.putrequest("GET", target, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", host_header)
        connection.putheader("User-Agent", user_agent)
        connection.putheader("Accept", "text/html,application/xhtml+xml,text/plain;q=0.9")
        connection.putheader("Accept-Encoding", "identity")
        connection.putheader("Connection", "close")
        connection.endheaders()
        response = connection.getresponse()
        declared_length = response.getheader("Content-Length")
        declared_bytes = None
        if declared_length:
            try:
                declared_bytes = int(declared_length)
            except ValueError:
                declared_bytes = None
        body = response.read(max_bytes + 1)
        truncated = len(body) > max_bytes or (declared_bytes is not None and declared_bytes > max_bytes)
        body = body[:max_bytes]
        headers = {key.lower(): value for key, value in response.getheaders()}
        return RawResponse("", response.status, headers, body, truncated=truncated)
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise WebResearchError(f"web request failed: {exc}") from exc
    finally:
        connection.close()


class WebFetcher:
    def __init__(
        self,
        *,
        limits: Limits | None = None,
        user_agent: str = "LambdaWebUI-WebResearch/0.1",
        resolver: Resolver = socket.getaddrinfo,
        request_once: RequestOnce | None = None,
        cache_ttl_seconds: float = 300,
        cache_max_entries: int = 64,
    ):
        self.limits = limits or Limits()
        self.user_agent = user_agent
        self.resolver = resolver
        self.request_once = request_once
        self.cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self.cache_max_entries = max(0, cache_max_entries)
        self._cache: OrderedDict[str, tuple[float, RawResponse]] = OrderedDict()

    def fetch_raw(self, url: str) -> RawResponse:
        initial = normalize_url(url)
        cached = self._cache.get(initial)
        if cached:
            created_at, response = cached
            if time.monotonic() - created_at <= self.cache_ttl_seconds:
                self._cache.move_to_end(initial)
                return response
            del self._cache[initial]
        current = initial
        for redirect_count in range(self.limits.max_redirects + 1):
            parsed = urlsplit(current)
            hostname = parsed.hostname or ""
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = resolve_public_addresses(hostname, port, resolver=self.resolver)
            target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            if self.request_once:
                response = self.request_once(
                    parsed.scheme,
                    hostname,
                    port,
                    addresses[0],
                    target,
                    self.limits.timeout_seconds,
                    self.limits.max_response_bytes,
                )
            else:
                response = _request_once(
                    parsed.scheme,
                    hostname,
                    port,
                    addresses[0],
                    target,
                    self.limits.timeout_seconds,
                    self.limits.max_response_bytes,
                    user_agent=self.user_agent,
                )
            response = RawResponse(
                current,
                response.status,
                response.headers,
                response.body,
                truncated=response.truncated,
            )
            if response.status in REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise WebResearchError("redirect response did not include a location")
                if redirect_count >= self.limits.max_redirects:
                    raise WebResearchError("too many redirects")
                current = normalize_url(urljoin(current, location))
                continue
            if response.status < 200 or response.status >= 300:
                raise WebResearchError(f"web server returned HTTP {response.status}")
            if self.cache_ttl_seconds > 0 and self.cache_max_entries > 0:
                self._cache[initial] = (time.monotonic(), response)
                self._cache.move_to_end(initial)
                while len(self._cache) > self.cache_max_entries:
                    self._cache.popitem(last=False)
            return response
        raise WebResearchError("too many redirects")

    def fetch_page(
        self,
        url: str,
        *,
        output_format: str = "markdown",
        max_characters: int | None = None,
        selector: str | None = None,
    ) -> dict[str, Any]:
        response = self.fetch_raw(url)
        content_encoding = response.headers.get("content-encoding", "identity").lower()
        if content_encoding not in {"", "identity"}:
            raise WebResearchError(f"unsupported content encoding: {content_encoding}")
        content_type, charset = _parse_content_type(response.headers.get("content-type", ""))
        if content_type not in SUPPORTED_CONTENT_TYPES:
            raise WebResearchError(
                f"unsupported content type: {content_type or 'missing content-type'}"
            )
        text = _decode_body(response.body, charset)
        requested_limit = max_characters or self.limits.max_output_characters
        if requested_limit < 1:
            raise WebResearchError("max_characters must be positive")
        character_limit = min(requested_limit, self.limits.max_output_characters)
        if content_type == "text/plain":
            content = text.strip()
            metadata = {"title": None, "description": None, "published_at": None, "modified_at": None}
            links: list[dict[str, str]] = []
        else:
            metadata, content, links = extract_html(
                text, response.url, output_format=output_format, selector=selector
            )
        if output_format not in {"markdown", "text", "metadata"}:
            raise WebResearchError("output_format must be markdown, text, or metadata")
        if output_format == "metadata":
            content = ""
        truncated = len(content) > character_limit
        if truncated:
            marker = "\n\n[Content truncated]"
            if character_limit <= len(marker):
                content = content[:character_limit]
            else:
                content = content[: character_limit - len(marker)].rstrip() + marker
        warnings: list[str] = []
        if response.truncated:
            warnings.append(
                f"source response exceeded {self.limits.max_response_bytes} bytes and was partially read"
            )
        if truncated:
            warnings.append(f"content truncated to {character_limit} characters")
        if content_type != "text/plain" and len(content.strip()) < 80:
            warnings.append(
                "little readable content was extracted; this page may require JavaScript rendering"
            )
        return {
            "url": response.url,
            "status": response.status,
            "content_type": content_type,
            **metadata,
            "content": content,
            "links": links,
            "truncated": truncated,
            "source_truncated": response.truncated,
            "warnings": warnings,
        }


def _parse_content_type(value: str) -> tuple[str, str | None]:
    parts = [part.strip() for part in value.split(";")]
    media_type = parts[0].lower() if parts and parts[0] else ""
    charset = None
    for part in parts[1:]:
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip(' "\'').lower()
    return media_type, charset


def _decode_body(body: bytes, declared_charset: str | None) -> str:
    encodings = [declared_charset] if declared_charset else []
    encodings.extend(["utf-8", "windows-1252"])
    for encoding in dict.fromkeys(item for item in encodings if item):
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _metadata_value(soup: BeautifulSoup, names: Iterable[str]) -> str | None:
    wanted = {name.lower() for name in names}
    for tag in soup.find_all("meta"):
        key = str(tag.get("property") or tag.get("name") or "").lower()
        if key in wanted and tag.get("content"):
            return html.unescape(str(tag["content"]).strip())
    return None


def _choose_content(soup: BeautifulSoup, selector: str | None) -> Tag:
    if selector:
        try:
            selected = soup.select_one(selector)
        except Exception as exc:
            raise WebResearchError(f"invalid content selector: {exc}") from exc
        if not selected:
            raise WebResearchError("content selector did not match an element")
        return selected
    for candidate in (
        soup.find("article"),
        soup.find("main"),
        soup.find(attrs={"role": "main"}),
        soup.body,
    ):
        if isinstance(candidate, Tag):
            return candidate
    return soup


def _inline_markdown(node: Tag | NavigableString, base_url: str) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    name = node.name.lower() if node.name else ""
    inner = "".join(_inline_markdown(child, base_url) for child in node.children)
    if name in {"strong", "b"}:
        return f"**{inner.strip()}**"
    if name in {"em", "i"}:
        return f"*{inner.strip()}*"
    if name == "code":
        return f"`{inner.strip()}`"
    if name == "a" and node.get("href"):
        label = inner.strip()
        href = normalize_link(base_url, str(node["href"]))
        return f"[{label}]({href})" if label and href else label
    if name == "br":
        return "\n"
    return inner


def _block_markdown(root: Tag, base_url: str) -> str:
    blocks: list[str] = []
    block_names = {"blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "pre"}
    candidates = root.find_all(block_names)
    if not candidates:
        return root.get_text("\n", strip=True)
    for node in candidates:
        if any(parent is not root and getattr(parent, "name", None) in block_names for parent in node.parents):
            continue
        value = re.sub(r"[ \t]+", " ", _inline_markdown(node, base_url)).strip()
        if not value:
            continue
        if node.name and node.name.startswith("h"):
            value = f"{'#' * int(node.name[1])} {value}"
        elif node.name == "li":
            value = f"- {value}"
        elif node.name == "blockquote":
            value = "\n".join(f"> {line}" for line in value.splitlines())
        elif node.name == "pre":
            value = f"```\n{node.get_text().strip()}\n```"
        blocks.append(value)
    return "\n\n".join(blocks)


def normalize_link(base_url: str, value: str) -> str | None:
    value = value.strip()
    if not value or value.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    try:
        return normalize_url(urljoin(base_url, value))
    except WebResearchError:
        return None


def extract_html(
    source: str,
    base_url: str,
    *,
    output_format: str = "markdown",
    selector: str | None = None,
) -> tuple[dict[str, str | None], str, list[dict[str, str]]]:
    soup = BeautifulSoup(source, "html.parser")
    title = _metadata_value(soup, ("og:title", "twitter:title"))
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    metadata = {
        "title": title or None,
        "description": _metadata_value(soup, ("description", "og:description", "twitter:description")),
        "published_at": _metadata_value(
            soup, ("article:published_time", "date", "datepublished", "publishdate")
        ),
        "modified_at": _metadata_value(
            soup, ("article:modified_time", "datemodified", "last-modified")
        ),
    }
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    root = _choose_content(soup, selector)
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in root.find_all("a", href=True):
        target = normalize_link(base_url, str(anchor["href"]))
        if target and target not in seen:
            seen.add(target)
            links.append({"url": target, "text": anchor.get_text(" ", strip=True)})
    if output_format == "text":
        content = root.get_text("\n", strip=True)
        content = re.sub(r"\n{3,}", "\n\n", content)
    else:
        content = _block_markdown(root, base_url)
    return metadata, content.strip(), links


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)


def crawl_website(
    fetcher: WebFetcher,
    start_url: str,
    *,
    max_depth: int = 1,
    max_pages: int = 10,
    same_origin_only: bool = True,
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    output_format: str = "markdown",
    robots_policy: str = "respect",
) -> dict[str, Any]:
    if max_depth < 0 or max_pages < 1:
        raise WebResearchError("max_depth must be non-negative and max_pages must be positive")
    depth_limit = min(max_depth, fetcher.limits.max_crawl_depth)
    page_limit = min(max_pages, fetcher.limits.max_crawl_pages)
    if robots_policy not in {"respect", "ignore"}:
        raise WebResearchError("robots_policy must be respect or ignore")
    try:
        include = re.compile(include_pattern) if include_pattern else None
        exclude = re.compile(exclude_pattern) if exclude_pattern else None
    except re.error as exc:
        raise WebResearchError(f"invalid URL pattern: {exc}") from exc
    start = normalize_url(start_url)
    start_origin = _origin(start)
    queue = deque([(start, 0, None)])
    queued = {start}
    visited: set[str] = set()
    pages: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    total_characters = 0
    robots: dict[tuple[str, str, int], RobotFileParser | None] = {}
    last_request: dict[tuple[str, str, int], float] = {}

    def allowed_by_robots(url: str) -> bool:
        if robots_policy == "ignore":
            return True
        origin = _origin(url)
        if origin not in robots:
            scheme, hostname, port = origin
            host = f"[{hostname}]" if ":" in hostname else hostname
            default_port = 443 if scheme == "https" else 80
            netloc = host if port == default_port else f"{host}:{port}"
            robots_url = urlunsplit((scheme, netloc, "/robots.txt", "", ""))
            parser = RobotFileParser()
            parser.set_url(robots_url)
            try:
                response = fetcher.fetch_raw(robots_url)
                content_type, charset = _parse_content_type(response.headers.get("content-type", ""))
                if content_type in {"text/plain", "text/html", ""}:
                    parser.parse(_decode_body(response.body, charset).splitlines())
                    robots[origin] = parser
                else:
                    robots[origin] = None
            except WebResearchError:
                robots[origin] = None
        parser = robots[origin]
        return parser is None or parser.can_fetch(fetcher.user_agent, url)

    while queue and len(pages) < page_limit:
        url, depth, parent = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        if same_origin_only and _origin(url) != start_origin:
            rejected.append({"url": url, "reason": "cross-origin link"})
            continue
        if include and not include.search(url):
            rejected.append({"url": url, "reason": "URL did not match include_pattern"})
            continue
        if exclude and exclude.search(url):
            rejected.append({"url": url, "reason": "URL matched exclude_pattern"})
            continue
        if not allowed_by_robots(url):
            rejected.append({"url": url, "reason": "disallowed by robots.txt"})
            continue
        origin = _origin(url)
        elapsed = time.monotonic() - last_request.get(origin, 0.0)
        if elapsed < fetcher.limits.crawl_delay_seconds:
            time.sleep(fetcher.limits.crawl_delay_seconds - elapsed)
        try:
            remaining = fetcher.limits.max_crawl_characters - total_characters
            if remaining <= 0:
                break
            page = fetcher.fetch_page(
                url,
                output_format=output_format,
                max_characters=min(fetcher.limits.max_output_characters, remaining),
            )
            last_request[origin] = time.monotonic()
        except WebResearchError as exc:
            failed.append({"url": url, "error": str(exc)})
            continue
        page["depth"] = depth
        page["parent_url"] = parent
        total_characters += len(page["content"])
        pages.append(page)
        if depth >= depth_limit:
            continue
        for link in page["links"]:
            target = link["url"]
            if target not in queued and target not in visited:
                queued.add(target)
                queue.append((target, depth + 1, page["url"]))

    return {
        "start_url": start,
        "pages": pages,
        "visited_total": len(visited),
        "returned_total": len(pages),
        "rejected": rejected,
        "failed": failed,
        "limits": {
            "max_depth": depth_limit,
            "max_pages": page_limit,
            "max_characters": fetcher.limits.max_crawl_characters,
        },
        "truncated": bool(queue) or total_characters >= fetcher.limits.max_crawl_characters,
    }
