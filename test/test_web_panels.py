import asyncio
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError

from open_webui.routers import web_panels as router
from open_webui.utils.web_panel_proxy import (
    create_panel_token,
    read_limited,
    rewrite_css,
    rewrite_html,
    validate_public_ip,
    verify_panel_token,
)


@pytest.mark.parametrize(
    'url',
    [
        'https://example.com/path?q=one',
        'http://localhost:3000/',
        'http://192.168.1.20/dashboard',
        '',
    ],
)
def test_panel_url_accepts_http_https_and_blank(url):
    assert router.WebPanelCreateForm(url=url).url == url


@pytest.mark.parametrize(
    'url',
    [
        'example.com',
        'javascript:alert(1)',
        'file:///etc/passwd',
        'https://user:secret@example.com/',
        'https:///missing-host',
    ],
)
def test_panel_url_rejects_unsafe_or_non_absolute_values(url):
    with pytest.raises(ValidationError):
        router.WebPanelCreateForm(url=url)


def test_update_form_is_partial_and_forbids_unknown_fields():
    assert router.WebPanelUpdateForm(title='Renamed').model_dump(exclude_unset=True) == {'title': 'Renamed'}
    with pytest.raises(ValidationError):
        router.WebPanelUpdateForm(unexpected=True)

    with pytest.raises(ValidationError):
        router.WebPanelUpdateForm(title='   ')


def test_get_endpoint_scopes_lookup_to_authenticated_user(monkeypatch):
    calls = []

    async def get(panel_id, user_id, db=None):
        calls.append((panel_id, user_id, db))
        return None

    monkeypatch.setattr(router.WebPanels, 'get', get)
    with pytest.raises(router.HTTPException) as error:
        asyncio.run(router.get_web_panel('panel-1', user=SimpleNamespace(id='user-1'), db=object()))
    assert error.value.status_code == 404
    assert calls[0][:2] == ('panel-1', 'user-1')


def test_batch_delete_deduplicates_ids_and_scopes_owner(monkeypatch):
    calls = []

    async def delete_many(ids, user_id, db=None):
        calls.append((ids, user_id, db))
        return len(ids)

    monkeypatch.setattr(router.WebPanels, 'delete_many', delete_many)
    result = asyncio.run(
        router.delete_web_panels(
            router.WebPanelBatchDeleteForm(ids=['one', 'two', 'one']),
            user=SimpleNamespace(id='user-1'),
            db=object(),
        )
    )
    assert result == {'success': True, 'deleted': 2}
    assert calls[0][:2] == (['one', 'two'], 'user-1')


def test_panel_session_token_is_signed_scoped_and_expires():
    token = create_panel_token('secret', 'panel-1', 'user-1', expires_at=2_000_000_000)
    assert verify_panel_token('secret', token, 'panel-1')['user_id'] == 'user-1'
    with pytest.raises(ValueError):
        verify_panel_token('other-secret', token, 'panel-1')
    with pytest.raises(ValueError):
        verify_panel_token('secret', token, 'panel-2')
    expired = create_panel_token('secret', 'panel-1', 'user-1', expires_at=1)
    with pytest.raises(ValueError):
        verify_panel_token('secret', expired, 'panel-1')


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'])
def test_proxy_rejects_non_public_addresses(address):
    with pytest.raises(ValueError):
        validate_public_ip(address)


def test_html_rewriter_removes_frame_policy_rewrites_resources_and_injects_bridge():
    rendered = rewrite_html(
        '<html><head><meta http-equiv="Content-Security-Policy" content="frame-ancestors none">'
        '<link rel="stylesheet" href="/site.css"></head><body><a href="/news">News</a>'
        '<img src="images/photo.jpg"></body></html>',
        'https://example.com/start',
        'panel-1',
        'signed-token',
    )
    assert 'Content-Security-Policy' not in rendered
    assert 'data-open-webui-panel-bridge' in rendered
    assert 'Summarize Page' in rendered
    assert 'Explain Text' in rendered
    assert 'Find Bias' in rendered
    assert 'Translate Text' not in rendered
    assert 'extractReadablePage' in rendered
    assert 'selectionContext' in rendered
    assert "document.querySelector('article, main, [role=\"main\"]')" in rendered
    assert 'name="referrer"' in rendered
    assert 'url=https%3A%2F%2Fexample.com%2Fnews' in rendered
    assert 'src="https://example.com/images/photo.jpg"' in rendered


def test_css_rewriter_bypasses_images_but_proxies_fonts():
    rendered = rewrite_css(
        'body { background: url("../image.png") } @font-face { src: url("font.woff2") }',
        'https://example.com/css/site.css',
        'panel-1',
        'signed-token',
    )
    assert 'url("https://example.com/image.png")' in rendered
    assert 'url=https%3A%2F%2Fexample.com%2Fcss%2Ffont.woff2' in rendered


def test_html_rewriter_promotes_lazy_images_and_makes_srcset_direct():
    rendered = rewrite_html(
        '<img data-src="/photo.jpg" data-srcset="/small.jpg 1x, /large.jpg 2x">',
        'https://example.com/news/story',
        'panel-1',
        'signed-token',
    )
    assert 'src="https://example.com/photo.jpg"' in rendered
    assert 'srcset="https://example.com/small.jpg 1x, https://example.com/large.jpg 2x"' in rendered
    assert '/api/v1/web-panels/' not in rendered.split('data-open-webui-panel-bridge', 1)[0]


def test_html_rewriter_preserves_commas_inside_srcset_urls():
    source = (
        'https://media.example/images/photo.jpg?c=16x9&q=h_438,w_780,c_fill/f_webp 1x, '
        'https://media.example/images/photo.jpg?c=16x9&q=h_876,w_1560,c_fill/f_webp 2x'
    )
    rendered = rewrite_html(
        f'<picture><source srcset="{source}" type="image/webp"></picture>',
        'https://example.com/',
        'panel-1',
        'signed-token',
    )
    assert BeautifulSoup(rendered, 'html.parser').source['srcset'] == source


def test_bounded_reader_collects_every_chunk_until_eof():
    class ChunkedStream:
        def __init__(self):
            self.chunks = [b'<head>', b'</head><body>', b'content</body>', b'']

        async def read(self, _size):
            return self.chunks.pop(0)

    assert asyncio.run(read_limited(ChunkedStream(), 100)) == (b'<head></head><body>content</body>')


def test_bounded_reader_rejects_combined_chunks_over_limit():
    class ChunkedStream:
        def __init__(self):
            self.chunks = [b'1234', b'5678', b'']

        async def read(self, _size):
            return self.chunks.pop(0)

    with pytest.raises(ValueError, match='too large'):
        asyncio.run(read_limited(ChunkedStream(), 7))
