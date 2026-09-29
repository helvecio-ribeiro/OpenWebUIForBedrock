import importlib.util
import os
import shutil
import socket
import sys
import uuid
from pathlib import Path

import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'examples/managed-mcp/web-research-tools'
STDIO_FIXTURE = ROOT / 'test/fixtures/web_research_stdio_server.py'


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    sys.path.insert(0, str(PACKAGE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(PACKAGE))
    return module


@pytest.fixture(scope='module')
def web():
    return load_module('web_research_example', 'web_research.py')


def public_resolver(host, port, *, type):
    assert type == socket.SOCK_STREAM
    addresses = {
        'example.com': '93.184.216.34',
        'other.example': '203.0.113.10',
    }
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (addresses[host], port))]


def test_manifest_registers_as_confined_package_local_service():
    manifest = yaml.safe_load((PACKAGE / 'mcp.yaml').read_text(encoding='utf-8'))

    assert manifest['schema_version'] == 1
    assert manifest['id'] == 'local-web-research'
    assert manifest['runtime']['args'] == ['run', '--frozen', 'server.py']
    assert manifest['security']['profile'] == 'confined'
    assert manifest['security']['read_only'] is False
    assert manifest['security']['root_write'] is False
    assert manifest['security']['filesystem_roots'] == ['./data']
    assert manifest['environment']['MCP_WEB_MAX_RESPONSE_BYTES']['default'] == '8388608'
    assert manifest['environment']['MCP_WEB_ARTIFACT_DIR']['default'] == 'data/artifacts'


@pytest.mark.parametrize(
    'url',
    [
        'file:///etc/passwd',
        'http://localhost/',
        'http://service.localhost/',
        'https://user:secret@example.com/',
        'javascript:alert(1)',
        '/relative/path',
    ],
)
def test_url_normalization_rejects_unsafe_forms(web, url):
    with pytest.raises(web.WebResearchError):
        web.normalize_url(url)


def test_url_normalization_removes_fragments_and_normalizes_defaults(web):
    assert web.normalize_url('HTTPS://Example.COM:443/a?q=1#fragment') == 'https://example.com/a?q=1'


@pytest.mark.parametrize(
    'address',
    [
        '127.0.0.1',
        '10.1.2.3',
        '169.254.169.254',
        '172.16.0.1',
        '192.168.1.1',
        '::1',
        'fe80::1',
        '::ffff:127.0.0.1',
        '0.0.0.0',
    ],
)
def test_resolution_rejects_local_private_and_metadata_addresses(web, address):
    def resolver(host, port, *, type):
        family = socket.AF_INET6 if ':' in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, '', (address, port))]

    with pytest.raises(web.WebResearchError, match='non-public'):
        web.resolve_public_addresses('attacker.example', 443, resolver=resolver)


def test_resolution_rejects_mixed_public_and_private_answers(web):
    def resolver(host, port, *, type):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port)),
        ]

    with pytest.raises(web.WebResearchError, match='non-public'):
        web.resolve_public_addresses('rebinding.example', 443, resolver=resolver)


def test_fetch_revalidates_redirect_destination_and_blocks_redirect_pivot(web):
    calls = []

    def resolver(host, port, *, type):
        address = '93.184.216.34' if host == 'example.com' else '169.254.169.254'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]

    def request_once(scheme, hostname, port, address, target, timeout, max_bytes):
        calls.append((hostname, address))
        return web.RawResponse('', 302, {'location': 'http://metadata.invalid/latest'}, b'')

    fetcher = web.WebFetcher(resolver=resolver, request_once=request_once)
    with pytest.raises(web.WebResearchError, match='non-public'):
        fetcher.fetch_raw('https://example.com/start')

    assert calls == [('example.com', '93.184.216.34')]


def test_html_extraction_returns_metadata_markdown_and_absolute_links(web):
    source = '''<!doctype html><html><head>
      <title>Fallback title</title>
      <meta property="og:title" content="Research title">
      <meta name="description" content="Useful description">
      <meta property="article:published_time" content="2026-09-28T12:00:00Z">
    </head><body><nav>Navigation</nav><article>
      <h1>Research title</h1>
      <p>A <strong>useful</strong> paragraph with <a href="/evidence">evidence</a>.</p>
      <ul><li>First point</li><li>Second point</li></ul>
      <script>ignoreMe()</script>
    </article></body></html>'''

    metadata, content, links = web.extract_html(source, 'https://example.com/article')

    assert metadata == {
        'title': 'Research title',
        'description': 'Useful description',
        'published_at': '2026-09-28T12:00:00Z',
        'modified_at': None,
    }
    assert '# Research title' in content
    assert '**useful**' in content
    assert '[evidence](https://example.com/evidence)' in content
    assert '- First point' in content
    assert 'Navigation' not in content
    assert 'ignoreMe' not in content
    assert links == [{'url': 'https://example.com/evidence', 'text': 'evidence'}]


def test_fetch_page_enforces_output_ceiling_and_reports_javascript_shell(web):
    html = b'<html><head><title>App</title></head><body><div id="app"></div></body></html>'

    def request_once(scheme, hostname, port, address, target, timeout, max_bytes):
        return web.RawResponse('', 200, {'content-type': 'text/html; charset=utf-8'}, html)

    fetcher = web.WebFetcher(
        limits=web.Limits(max_output_characters=20),
        resolver=public_resolver,
        request_once=request_once,
    )
    result = fetcher.fetch_page('https://example.com/app', max_characters=10_000)

    assert result['url'] == 'https://example.com/app'
    assert result['title'] == 'App'
    assert result['content'] == ''
    assert any('JavaScript rendering' in warning for warning in result['warnings'])


def test_fetch_page_rejects_binary_content(web):
    def request_once(scheme, hostname, port, address, target, timeout, max_bytes):
        return web.RawResponse('', 200, {'content-type': 'application/octet-stream'}, b'binary')

    fetcher = web.WebFetcher(resolver=public_resolver, request_once=request_once)
    with pytest.raises(web.WebResearchError, match='unsupported content type'):
        fetcher.fetch_page('https://example.com/file')


def test_fetch_page_never_exceeds_character_ceiling(web):
    body = ('<html><body><main><p>' + ('content ' * 100) + '</p></main></body></html>').encode()

    def request_once(scheme, hostname, port, address, target, timeout, max_bytes):
        return web.RawResponse('', 200, {'content-type': 'text/html'}, body)

    fetcher = web.WebFetcher(
        limits=web.Limits(max_output_characters=80),
        resolver=public_resolver,
        request_once=request_once,
    )
    result = fetcher.fetch_page('https://example.com/long', max_characters=10_000)

    assert result['truncated'] is True
    assert len(result['content']) <= 80
    assert result['content'].endswith('[Content truncated]')


def test_fetch_page_extracts_partial_oversized_html_and_reports_source_truncation(web):
    body = (
        b'<html><body><main><h1>Headlines</h1><p>First useful story with enough '
        b'visible article text to be recognized as meaningful extracted page content.</p>'
    )

    def request_once(scheme, hostname, port, address, target, timeout, max_bytes):
        return web.RawResponse(
            '',
            200,
            {'content-type': 'text/html', 'content-length': '9000000'},
            body,
            truncated=True,
        )

    fetcher = web.WebFetcher(
        limits=web.Limits(max_response_bytes=32, max_output_characters=2000),
        resolver=public_resolver,
        request_once=request_once,
    )
    result = fetcher.fetch_page('https://example.com/large', max_characters=2000)

    assert 'First useful story' in result['content']
    assert result['source_truncated'] is True
    assert result['truncated'] is False
    assert result['warnings'] == [
        'source response exceeded 32 bytes and was partially read'
    ]


def test_fetch_page_rejects_unrequested_compression(web):
    def request_once(scheme, hostname, port, address, target, timeout, max_bytes):
        return web.RawResponse(
            '',
            200,
            {'content-type': 'text/html', 'content-encoding': 'gzip'},
            b'not-decoded',
        )

    fetcher = web.WebFetcher(resolver=public_resolver, request_once=request_once)
    with pytest.raises(web.WebResearchError, match='unsupported content encoding'):
        fetcher.fetch_page('https://example.com/compressed')


def test_successful_fetches_use_the_bounded_response_cache(web):
    calls = []

    def request_once(scheme, hostname, port, address, target, timeout, max_bytes):
        calls.append(target)
        return web.RawResponse('', 200, {'content-type': 'text/plain'}, b'cached content')

    fetcher = web.WebFetcher(
        resolver=public_resolver,
        request_once=request_once,
        cache_ttl_seconds=60,
        cache_max_entries=1,
    )

    assert fetcher.fetch_page('https://example.com/one')['content'] == 'cached content'
    assert fetcher.fetch_page('https://example.com/one')['content'] == 'cached content'
    fetcher.fetch_page('https://example.com/two')
    fetcher.fetch_page('https://example.com/one')

    assert calls == ['/one', '/two', '/one']


def test_crawl_is_bounded_deduplicated_and_same_origin(web):
    pages = {
        '/': b'''<html><body><main><h1>Home</h1><p>Home content is long enough.</p>
            <a href="/one#section">One</a><a href="/one">Duplicate</a>
            <a href="https://other.example/out">Outside</a></main></body></html>''',
        '/one': b'''<html><body><article><h1>One</h1><p>Child page content.</p>
            <a href="/two">Two</a><a href="/">Home</a></article></body></html>''',
        '/two': b'<html><body><article><h1>Two</h1><p>Too deep.</p></article></body></html>',
    }

    def request_once(scheme, hostname, port, address, target, timeout, max_bytes):
        return web.RawResponse('', 200, {'content-type': 'text/html'}, pages[target])

    fetcher = web.WebFetcher(
        limits=web.Limits(max_crawl_depth=2, max_crawl_pages=10, crawl_delay_seconds=0),
        resolver=public_resolver,
        request_once=request_once,
    )
    result = web.crawl_website(
        fetcher,
        'https://example.com/',
        max_depth=1,
        max_pages=10,
        robots_policy='ignore',
    )

    assert [page['url'] for page in result['pages']] == [
        'https://example.com/',
        'https://example.com/one',
    ]
    assert result['pages'][1]['parent_url'] == 'https://example.com/'
    assert {item['url'] for item in result['rejected']} == {'https://other.example/out'}
    assert all(page['url'] != 'https://example.com/two' for page in result['pages'])


def test_artifact_store_reads_searches_and_hides_storage_paths(tmp_path):
    artifacts = load_module('web_research_artifacts', 'artifact_store.py')
    store = artifacts.ResearchArtifactStore(
        tmp_path / 'private-artifacts',
        max_read_characters=12,
        max_search_matches=1,
        max_search_context_characters=8,
    )
    metadata = store.create(
        'crawl',
        [
            {
                'url': 'https://example.com/one',
                'title': 'One',
                'content': 'Alpha evidence and more evidence.',
                'truncated': False,
            },
            {
                'url': 'https://example.com/two',
                'title': 'Two',
                'content': 'Second source.',
                'truncated': True,
            },
        ],
    )

    assert metadata['id'].startswith('wr_')
    assert metadata['source_count'] == 2
    assert metadata['sources'][1]['index'] == 1
    assert metadata['sources'][1]['truncated'] is True
    assert str(tmp_path) not in str(metadata)

    first = store.read(metadata['id'], source_index=0, max_characters=100)
    assert first['content'] == 'Alpha eviden'
    assert first['end'] == 12
    assert first['has_more'] is True
    continued = store.read(metadata['id'], source_index=0, offset=first['end'])
    assert continued['content'].startswith('ce and more')

    matches = store.search(metadata['id'], 'evidence', max_matches=10, context_characters=100)
    assert matches['returned_matches'] == 1
    assert matches['total_matches'] == 2
    assert matches['truncated'] is True
    assert matches['matches'][0]['source_index'] == 0
    assert len(matches['matches'][0]['excerpt']) <= len('evidence') + 16
    assert str(tmp_path) not in str(matches)


def test_artifact_store_expires_and_evicts_oldest_entries(tmp_path):
    artifacts = load_module('web_research_artifact_expiry', 'artifact_store.py')
    now = [1000.0]
    store = artifacts.ResearchArtifactStore(
        tmp_path / 'artifacts',
        ttl_seconds=10,
        max_artifacts=1,
        clock=lambda: now[0],
    )
    first = store.create('page', [{'url': 'https://example.com/one', 'content': 'one'}])
    second = store.create('page', [{'url': 'https://example.com/two', 'content': 'two'}])

    with pytest.raises(artifacts.ArtifactError, match='not found or has expired'):
        store.read(first['id'])
    assert store.read(second['id'])['content'] == 'two'

    now[0] = 1011.0
    assert store.cleanup() == 1
    with pytest.raises(artifacts.ArtifactError, match='not found or has expired'):
        store.read(second['id'])
    with pytest.raises(artifacts.ArtifactError, match='invalid artifact ID'):
        store.read('../../etc/passwd')


def test_server_externalizes_large_results_and_advertises_artifact_tools(monkeypatch):
    monkeypatch.setenv('MCP_WEB_CRAWL_DELAY_SECONDS', '0')
    artifact_directory = PACKAGE / 'data' / 'pytest-artifacts'
    shutil.rmtree(artifact_directory, ignore_errors=True)
    monkeypatch.setenv('MCP_WEB_ARTIFACT_DIR', 'data/pytest-artifacts')
    monkeypatch.setenv('MCP_WEB_INLINE_CHARACTERS', '40')
    server = load_module('web_research_server_example', 'server.py')
    tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}

    assert set(tools) == {
        'fetch_web_page',
        'crawl_website',
        'read_web_artifact',
        'search_web_artifact',
    }
    assert all(tool.description for tool in tools.values())
    assert all(
        schema.get('description')
        for tool in tools.values()
        for schema in tool.parameters.get('properties', {}).values()
    )

    page = {
        'url': 'https://example.com/article',
        'title': 'Article',
        'content': 'important evidence ' * 10,
        'truncated': False,
        'source_truncated': False,
        'warnings': [],
    }
    response = server._externalize_page(page)
    assert response['inline_truncated'] is True
    assert len(response['content']) <= 40
    assert response['artifact']['source_count'] == 1
    artifact_id = response['artifact']['id']
    assert server.read_web_artifact(artifact_id, max_characters=10)['content'] == 'important '
    search = server.search_web_artifact(artifact_id, 'evidence')
    assert search['total_matches'] == 10

    crawl_response = server._externalize_crawl(
        {
            'start_url': 'https://example.com/',
            'pages': [
                {'url': 'https://example.com/', 'title': 'Home', 'content': 'a' * 30},
                {'url': 'https://example.com/two', 'title': 'Two', 'content': 'b' * 30},
            ],
            'truncated': False,
        }
    )
    assert crawl_response['inline_truncated'] is True
    assert sum(len(page['content']) for page in crawl_response['pages']) == 40
    assert crawl_response['artifact']['source_count'] == 2
    shutil.rmtree(artifact_directory, ignore_errors=True)


@pytest.mark.anyio
async def test_all_tools_work_through_mcp_client_session_over_stdio():
    artifact_name = f'pytest-stdio-{uuid.uuid4().hex}'
    artifact_directory = PACKAGE / 'data' / artifact_name
    environment = {
        **os.environ,
        'PYTHONPATH': str(PACKAGE),
        'MCP_WEB_ARTIFACT_DIR': f'data/{artifact_name}',
        'MCP_WEB_INLINE_CHARACTERS': '80',
        'MCP_WEB_CRAWL_DELAY_SECONDS': '0',
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(STDIO_FIXTURE)],
        env=environment,
        cwd=str(PACKAGE),
    )

    try:
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == 'Local Web Research'

                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == {
                    'fetch_web_page',
                    'crawl_website',
                    'read_web_artifact',
                    'search_web_artifact',
                }

                fetched = await session.call_tool(
                    'fetch_web_page',
                    {'url': 'https://fixture.example/', 'output_format': 'text'},
                )
                assert fetched.isError is False
                assert fetched.structuredContent is not None
                assert fetched.structuredContent['inline_truncated'] is True
                artifact = fetched.structuredContent['artifact']
                assert artifact['source_count'] == 1

                searched = await session.call_tool(
                    'search_web_artifact',
                    {
                        'artifact_id': artifact['id'],
                        'query': 'fixture evidence',
                        'max_matches': 2,
                        'context_characters': 20,
                    },
                )
                assert searched.isError is False
                assert searched.structuredContent['returned_matches'] == 2

                read = await session.call_tool(
                    'read_web_artifact',
                    {
                        'artifact_id': artifact['id'],
                        'source_index': 0,
                        'offset': 0,
                        'max_characters': 32,
                    },
                )
                assert read.isError is False
                assert read.structuredContent['content'] == 'Deterministic fixture evidence. '
                assert read.structuredContent['has_more'] is True

                crawled = await session.call_tool(
                    'crawl_website',
                    {
                        'start_url': 'https://fixture.example/',
                        'max_depth': 1,
                        'max_pages': 2,
                        'output_format': 'text',
                    },
                )
                assert crawled.isError is False
                assert crawled.structuredContent['returned_total'] == 2
                assert [page['url'] for page in crawled.structuredContent['pages']] == [
                    'https://fixture.example/',
                    'https://fixture.example/child',
                ]
    finally:
        shutil.rmtree(artifact_directory, ignore_errors=True)
