import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import re
import socket
import time
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup
from aiohttp.abc import AbstractResolver

MAX_RESPONSE_BYTES = 10 * 1024 * 1024
TOKEN_TTL_SECONDS = 30 * 60

# Browser-safe, passive assets can be fetched directly from their origin. Keeping
# them out of the application proxy avoids needless bandwidth and preserves CDN
# behaviour. Active content and fonts stay proxied so URL rewriting, CORS and the
# Web Panel bridge continue to work consistently.
DIRECT_ASSET_EXTENSIONS = {
    '.avif',
    '.bmp',
    '.gif',
    '.ico',
    '.jpeg',
    '.jpg',
    '.m4a',
    '.mp3',
    '.mp4',
    '.ogg',
    '.png',
    '.svg',
    '.wav',
    '.webm',
    '.webp',
}
DIRECT_ASSET_ATTRIBUTES = {
    ('audio', 'src'),
    ('img', 'src'),
    ('source', 'src'),
    ('video', 'poster'),
    ('video', 'src'),
}
LAZY_IMAGE_ATTRIBUTES = ('data-src', 'data-lazy-src', 'data-original', 'data-url')
LAZY_SRCSET_ATTRIBUTES = ('data-srcset', 'data-lazy-srcset')


async def read_limited(stream, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    chunks = []
    size = 0
    while True:
        chunk = await stream.read(min(64 * 1024, limit - size + 1))
        if not chunk:
            return b''.join(chunks)
        size += len(chunk)
        if size > limit:
            raise ValueError('Remote response is too large')
        chunks.append(chunk)


class PublicNetworkResolver(AbstractResolver):
    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM, family=family)
        results = []
        for resolved_family, _, protocol, _, address in infos:
            validate_public_ip(address[0])
            results.append(
                {
                    'hostname': host,
                    'host': address[0],
                    'port': address[1],
                    'family': resolved_family,
                    'proto': protocol,
                    'flags': socket.AI_NUMERICHOST,
                }
            )
        return results

    async def close(self):
        return None


def create_panel_token(secret: str, panel_id: str, user_id: str, expires_at: int | None = None) -> str:
    payload = {'panel_id': panel_id, 'user_id': user_id, 'exp': expires_at or int(time.time()) + TOKEN_TTL_SECONDS}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).rstrip(b'=')
    signature = hmac.new(secret.encode(), encoded, hashlib.sha256).digest()
    return f'{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b"=").decode()}'


def verify_panel_token(secret: str, token: str, panel_id: str) -> dict:
    try:
        encoded_text, signature_text = token.split('.', 1)
        encoded = encoded_text.encode()
        signature = base64.urlsafe_b64decode(signature_text + '=' * (-len(signature_text) % 4))
        expected = hmac.new(secret.encode(), encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError('Invalid panel token')
        payload = json.loads(base64.urlsafe_b64decode(encoded_text + '=' * (-len(encoded_text) % 4)))
        if payload.get('panel_id') != panel_id or int(payload.get('exp', 0)) < int(time.time()):
            raise ValueError('Expired or mismatched panel token')
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid panel token') from exc


def validate_public_ip(address: str) -> None:
    ip = ipaddress.ip_address(address)
    if not ip.is_global:
        raise ValueError('Web Panels cannot access private or local network addresses')


async def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only public HTTP and HTTPS URLs are supported')
    if parsed.port and parsed.port not in {80, 443}:
        raise ValueError('Only standard HTTP and HTTPS ports are supported')
    infos = await asyncio.get_running_loop().getaddrinfo(
        parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM
    )
    for info in infos:
        validate_public_ip(info[4][0])
    return url


def proxy_url(panel_id: str, token: str, target: str) -> str:
    return f'/api/v1/web-panels/{panel_id}/content?token={quote(token)}&url={quote(target, safe="")}'


def _rewrite_url(value: str, base_url: str, panel_id: str, token: str) -> str:
    value = value.strip()
    if not value or value.startswith(('#', 'data:', 'blob:', 'javascript:', 'mailto:', 'tel:')):
        return value
    absolute = urljoin(base_url, value)
    if urlparse(absolute).scheme not in {'http', 'https'}:
        return value
    return proxy_url(panel_id, token, absolute)


def _absolute_remote_url(value: str, base_url: str) -> str:
    value = value.strip()
    if not value or value.startswith(('#', 'data:', 'blob:', 'javascript:', 'mailto:', 'tel:')):
        return value
    absolute = urljoin(base_url, value)
    return absolute if urlparse(absolute).scheme in {'http', 'https'} else value


def _is_direct_asset(value: str, base_url: str) -> bool:
    path = urlparse(urljoin(base_url, value)).path.lower()
    return any(path.endswith(extension) for extension in DIRECT_ASSET_EXTENSIONS)


def _rewrite_asset_url(value: str, base_url: str, panel_id: str, token: str) -> str:
    if _is_direct_asset(value, base_url):
        return _absolute_remote_url(value, base_url)
    return _rewrite_url(value, base_url, panel_id, token)


def _rewrite_srcset(value: str, base_url: str, *, direct: bool, panel_id: str, token: str) -> str:
    rewrite = _absolute_remote_url if direct else lambda url, base: _rewrite_asset_url(url, base, panel_id, token)
    # A comma inside a URL is legal and is commonly used by image CDNs for
    # transformation parameters. Srcset candidate separators are followed by
    # whitespace in normal HTML, so do not split every comma unconditionally.
    candidates = re.split(r',(?=\s+\S)', value)
    return ', '.join(
        f'{rewrite(parts[0], base_url)} {" ".join(parts[1:])}'.strip()
        for candidate in candidates
        if (parts := candidate.strip().split())
    )


def rewrite_css(css: str, base_url: str, panel_id: str, token: str) -> str:
    return re.sub(
        r'url\(\s*(["\']?)([^"\')]+)\1\s*\)',
        lambda match: f'url("{_rewrite_asset_url(match.group(2), base_url, panel_id, token)}")',
        css,
        flags=re.IGNORECASE,
    )


def selection_bridge(remote_url: str, panel_id: str, token: str) -> str:
    encoded_url = json.dumps(remote_url)
    encoded_prefix = json.dumps(f'/api/v1/web-panels/{panel_id}/content?token={quote(token)}&url=')
    return f"""
<script data-open-webui-panel-bridge>
(() => {{
  const remoteUrl = {encoded_url};
  const proxyPrefix = {encoded_prefix};
  const throughProxy = (value) => {{
    const raw = typeof value === 'string' ? value : value?.url;
    if (!raw || raw.startsWith(proxyPrefix)) return raw;
    return proxyPrefix + encodeURIComponent(new URL(raw, remoteUrl).href);
  }};
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => nativeFetch(throughProxy(input), init);
  const nativeOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {{ return nativeOpen.call(this, method, throughProxy(url), ...rest); }};
  const send = (message) => parent.postMessage({{ source: 'open-webui-web-panel', ...message }}, '*');
  window.addEventListener('message', (event) => {{
    const message = event.data;
    if (event.source !== parent || message?.source !== 'open-webui-web-panel-host' || message?.type !== 'navigation') return;
    if (message.action === 'back') history.back();
    else if (message.action === 'forward') history.forward();
    else if (message.action === 'reload') location.reload();
  }});
  let selectionRect = null;
  let selectionContext = '';
  const extractReadablePage = () => {{
    const root = document.querySelector('article, main, [role="main"]') || document.body;
    if (!root) return '';
    const copy = root.cloneNode(true);
    copy.querySelectorAll([
      'script', 'style', 'noscript', 'template', 'svg', 'canvas', 'iframe',
      'nav', 'header', 'footer', 'aside', 'form', 'button', 'input', 'select', 'textarea',
      '[role="navigation"]', '[role="banner"]', '[role="complementary"]',
      '[aria-hidden="true"]', '[hidden]',
      '[class*="advert"]', '[class*="promo"]', '[class*="newsletter"]',
      '[class*="social"]', '[class*="share"]', '[class*="related"]',
      '[id*="advert"]', '[id*="promo"]', '[id*="newsletter"]'
    ].join(',')).forEach((element) => element.remove());
    const blocks = [...copy.querySelectorAll('h1, h2, h3, p, blockquote, figcaption, li')];
    const candidates = blocks.length
      ? blocks.map((element) => element.textContent || '')
      : [copy.textContent || ''];
    const seen = new Set();
    return candidates
      .map((text) => text.replace(/\\s+/g, ' ').trim())
      .filter((text) => text.length > 20 && !seen.has(text) && seen.add(text))
      .join('\\n\\n')
      .slice(0, 50000);
  }};
  send({{ type: 'navigated', url: remoteUrl, title: document.title }});
  const menu = document.createElement('div');
  Object.assign(menu.style, {{position:'fixed',display:'none',zIndex:'2147483647',background:'#18181b',color:'white',padding:'5px',borderRadius:'10px',boxShadow:'0 6px 22px #0006',font:'13px system-ui'}});
  const actions = [
    {{label:'Explain Text', action:'explain'}},
    {{label:'Find Bias', action:'find-bias'}},
    {{label:'Challenge Text', action:'challenge'}},
    {{label:'Summarize Page', action:'summarize-page'}}
  ];
  for (const item of actions) {{
    const button = document.createElement('button');
    button.textContent = item.label;
    Object.assign(button.style, {{background:'transparent',color:'white',border:'0',padding:'7px 10px',cursor:'pointer'}});
    button.onclick = (event) => {{
      event.stopPropagation();
      const selection = window.getSelection();
      const selectedText = selection ? selection.toString().trim() : '';
      const text = item.action === 'summarize-page'
        ? extractReadablePage()
        : selectedText;
      if (text) send({{type:'selection-action', action:item.action, text, context:selectionContext, url:remoteUrl, title:document.title, rect:selectionRect}});
      menu.style.display = 'none';
    }};
    menu.appendChild(button);
  }}
  document.documentElement.appendChild(menu);
  document.addEventListener('mouseup', () => {{
    setTimeout(() => {{
      const selection = window.getSelection();
      const text = selection ? selection.toString().trim() : '';
      if (!text) {{ menu.style.display = 'none'; return; }}
      const range = selection.getRangeAt(0); const rect = range.getBoundingClientRect();
      selectionRect = {{left:rect.left, top:rect.top, right:rect.right, bottom:rect.bottom, width:rect.width, height:rect.height}};
      const contextNode = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
        ? range.commonAncestorContainer
        : range.commonAncestorContainer.parentElement;
      selectionContext = (contextNode?.closest?.('p, li, blockquote, section, article')?.innerText || text)
        .replace(/\\s+/g, ' ').trim().slice(0, 4000);
      menu.style.display = 'block';
      const menuWidth = menu.offsetWidth;
      const menuHeight = menu.offsetHeight;
      menu.style.left = Math.max(8, Math.min(rect.left, innerWidth - menuWidth - 8)) + 'px';
      const below = rect.bottom + 7;
      const top = below + menuHeight + 8 <= innerHeight ? below : rect.top - menuHeight - 7;
      menu.style.top = Math.max(8, Math.min(top, innerHeight - menuHeight - 8)) + 'px';
    }}, 0);
  }});
  document.addEventListener('mousedown', (event) => {{ if (!menu.contains(event.target)) menu.style.display='none'; }});
}})();
</script>"""


def rewrite_html(html: str, base_url: str, panel_id: str, token: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for meta in soup.find_all('meta'):
        if str(meta.get('http-equiv', '')).lower() in {'content-security-policy', 'x-frame-options'}:
            meta.decompose()
    head = soup.head or soup
    referrer = soup.new_tag('meta')
    referrer.attrs['name'] = 'referrer'
    referrer.attrs['content'] = 'no-referrer'
    head.insert(0, referrer)
    for tag, attribute in [
        ('a', 'href'),
        ('link', 'href'),
        ('script', 'src'),
        ('img', 'src'),
        ('source', 'src'),
        ('video', 'src'),
        ('video', 'poster'),
        ('audio', 'src'),
        ('iframe', 'src'),
        ('form', 'action'),
    ]:
        for element in soup.find_all(tag):
            if element.get(attribute):
                if (tag, attribute) in DIRECT_ASSET_ATTRIBUTES:
                    element[attribute] = _absolute_remote_url(element[attribute], base_url)
                else:
                    element[attribute] = _rewrite_url(element[attribute], base_url, panel_id, token)
    for image in soup.find_all('img'):
        for attribute in LAZY_IMAGE_ATTRIBUTES:
            if image.get(attribute):
                image[attribute] = _absolute_remote_url(image[attribute], base_url)
        if not image.get('src'):
            lazy_source = next(
                (image.get(attribute) for attribute in LAZY_IMAGE_ATTRIBUTES if image.get(attribute)), None
            )
            if lazy_source:
                image['src'] = lazy_source
        for attribute in LAZY_SRCSET_ATTRIBUTES:
            if image.get(attribute):
                image[attribute] = _rewrite_srcset(
                    image[attribute], base_url, direct=True, panel_id=panel_id, token=token
                )
        if not image.get('srcset'):
            lazy_srcset = next(
                (image.get(attribute) for attribute in LAZY_SRCSET_ATTRIBUTES if image.get(attribute)), None
            )
            if lazy_srcset:
                image['srcset'] = lazy_srcset
    for element in soup.find_all(srcset=True):
        element['srcset'] = _rewrite_srcset(
            element['srcset'],
            base_url,
            direct=element.name in {'img', 'source'},
            panel_id=panel_id,
            token=token,
        )
    for style in soup.find_all('style'):
        if style.string:
            style.string.replace_with(rewrite_css(style.string, base_url, panel_id, token))
    bridge = BeautifulSoup(selection_bridge(base_url, panel_id, token), 'html.parser')
    (soup.body or soup).append(bridge)
    return str(soup)
