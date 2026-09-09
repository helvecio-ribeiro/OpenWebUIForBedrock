from typing import Optional
from urllib.parse import urlparse

import aiohttp

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from open_webui.env import WEBUI_SECRET_KEY
from open_webui.internal.db import get_async_session
from open_webui.models.web_panels import WebPanelModel, WebPanels
from open_webui.utils.auth import get_verified_user
from open_webui.utils.web_panel_proxy import (
    MAX_RESPONSE_BYTES,
    PublicNetworkResolver,
    create_panel_token,
    proxy_url,
    read_limited,
    rewrite_css,
    rewrite_html,
    validate_public_url,
    verify_panel_token,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_panel_cookies: dict[str, dict[str, dict[str, str]]] = {}


def validate_panel_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ''
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('URL must be an absolute HTTP or HTTPS URL')
    if parsed.username or parsed.password:
        raise ValueError('Credentials must not be embedded in the URL')
    return value


class WebPanelCreateForm(BaseModel):
    title: str = Field(default='New Tab', min_length=1, max_length=200)
    url: str = ''
    model_config = ConfigDict(extra='forbid')

    @field_validator('url')
    @classmethod
    def validate_url(cls, value):
        return validate_panel_url(value)

    @field_validator('title')
    @classmethod
    def validate_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Title must not be blank')
        return value


class WebPanelUpdateForm(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    url: Optional[str] = None
    model_config = ConfigDict(extra='forbid')

    @field_validator('url')
    @classmethod
    def validate_url(cls, value):
        return validate_panel_url(value) if value is not None else value

    @field_validator('title')
    @classmethod
    def validate_title(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError('Title must not be blank')
        return value


class WebPanelBatchDeleteForm(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    model_config = ConfigDict(extra='forbid')


class WebPanelSessionResponse(BaseModel):
    token: str
    proxy_url: str


@router.get('/', response_model=list[WebPanelModel])
async def list_web_panels(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    return await WebPanels.list(user.id, db=db)


@router.post('/', response_model=WebPanelModel)
async def create_web_panel(
    form: WebPanelCreateForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    return await WebPanels.create(user.id, form.title.strip(), form.url, db=db)


@router.get('/{panel_id}', response_model=WebPanelModel)
async def get_web_panel(panel_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    panel = await WebPanels.get(panel_id, user.id, db=db)
    if not panel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Web panel not found')
    return panel


@router.patch('/{panel_id}', response_model=WebPanelModel)
async def update_web_panel(
    panel_id: str,
    form: WebPanelUpdateForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    values = form.model_dump(exclude_unset=True)
    if 'title' in values:
        values['title'] = values['title'].strip()
    panel = await WebPanels.update(panel_id, user.id, values, db=db)
    if not panel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Web panel not found')
    return panel


@router.delete('/{panel_id}')
async def delete_web_panel(
    panel_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    if not await WebPanels.delete(panel_id, user.id, db=db):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Web panel not found')
    return {'success': True}


@router.post('/batch/delete')
async def delete_web_panels(
    form: WebPanelBatchDeleteForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    ids = list(dict.fromkeys(form.ids))
    deleted = await WebPanels.delete_many(ids, user.id, db=db)
    return {'success': True, 'deleted': deleted}


@router.post('/{panel_id}/session', response_model=WebPanelSessionResponse)
async def create_web_panel_session(
    panel_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    panel = await WebPanels.get(panel_id, user.id, db=db)
    if not panel:
        raise HTTPException(status_code=404, detail='Web panel not found')
    token = create_panel_token(WEBUI_SECRET_KEY, panel.id, user.id)
    return WebPanelSessionResponse(token=token, proxy_url=proxy_url(panel.id, token, panel.url) if panel.url else '')


@router.api_route('/{panel_id}/content', methods=['GET', 'POST', 'OPTIONS'])
async def proxy_web_panel_content(panel_id: str, request: Request, token: str, url: str):
    if request.method == 'OPTIONS':
        return Response(
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': '*',
            }
        )
    try:
        verify_panel_token(WEBUI_SECRET_KEY, token, panel_id)
        await validate_public_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    panel = await WebPanels.get_by_id(panel_id)
    if not panel:
        raise HTTPException(status_code=404, detail='Web panel not found')

    method = request.method
    body = await request.body() if method == 'POST' else None
    forward_headers = {
        'Accept': request.headers.get('accept', '*/*'),
        'Accept-Language': request.headers.get('accept-language', 'en-US,en;q=0.9'),
        'User-Agent': request.headers.get('user-agent', 'Open WebUI Web Panel'),
    }
    if request.headers.get('content-type'):
        forward_headers['Content-Type'] = request.headers['content-type']
    host = urlparse(url).hostname or ''
    cookies = _panel_cookies.get(panel_id, {}).get(host, {})
    if cookies:
        forward_headers['Cookie'] = '; '.join(f'{name}={value}' for name, value in cookies.items())

    timeout = aiohttp.ClientTimeout(total=20, connect=8)
    current_url = url
    try:
        connector = aiohttp.TCPConnector(resolver=PublicNetworkResolver())
        async with aiohttp.ClientSession(timeout=timeout, auto_decompress=True, connector=connector) as session:
            for _ in range(6):
                await validate_public_url(current_url)
                async with session.request(
                    method, current_url, data=body, headers=forward_headers, allow_redirects=False
                ) as upstream:
                    if upstream.status in {301, 302, 303, 307, 308} and upstream.headers.get('Location'):
                        from urllib.parse import urljoin

                        current_url = urljoin(current_url, upstream.headers['Location'])
                        if upstream.status in {301, 302, 303}:
                            method, body = 'GET', None
                        continue
                    try:
                        content = await read_limited(upstream.content, MAX_RESPONSE_BYTES)
                    except ValueError as exc:
                        raise HTTPException(status_code=413, detail=str(exc)) from exc
                    if upstream.cookies:
                        host_cookies = _panel_cookies.setdefault(panel_id, {}).setdefault(
                            urlparse(current_url).hostname or '', {}
                        )
                        for name, morsel in upstream.cookies.items():
                            host_cookies[name] = morsel.value
                    content_type = upstream.headers.get('Content-Type', 'application/octet-stream')
                    if 'text/html' in content_type:
                        charset = upstream.charset or 'utf-8'
                        content = rewrite_html(
                            content.decode(charset, errors='replace'), current_url, panel_id, token
                        ).encode('utf-8')
                        content_type = 'text/html; charset=utf-8'
                    elif 'text/css' in content_type:
                        charset = upstream.charset or 'utf-8'
                        content = rewrite_css(
                            content.decode(charset, errors='replace'), current_url, panel_id, token
                        ).encode('utf-8')
                        content_type = 'text/css; charset=utf-8'
                    return Response(
                        content=content,
                        status_code=upstream.status,
                        media_type=None,
                        headers={
                            'Content-Type': content_type,
                            'Cache-Control': 'no-store',
                            'Access-Control-Allow-Origin': '*',
                            'Cross-Origin-Resource-Policy': 'cross-origin',
                        },
                    )
            raise HTTPException(status_code=508, detail='Too many redirects')
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=502, detail=f'Unable to load remote content: {exc}') from exc
