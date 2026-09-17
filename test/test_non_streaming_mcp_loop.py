import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from open_webui.utils import middleware


def tool_response(arguments='{}'):
    return {
        'choices': [
            {
                'message': {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': [
                        {
                            'id': 'call-1',
                            'type': 'function',
                            'function': {
                                'name': 'local-system-tools_get_current_datetime',
                                'arguments': arguments,
                            },
                        }
                    ],
                }
            }
        ]
    }


def make_context(tool_callable):
    return {
        'request': SimpleNamespace(state=SimpleNamespace(max_tool_call_iterations=4)),
        'user': None,
        'form_data': {
            'model': 'bedrock:test',
            'stream': False,
            'messages': [{'role': 'user', 'content': 'What day is today?'}],
        },
        'metadata': {
            'tools': {
                'local-system-tools_get_current_datetime': {
                    'type': 'mcp',
                    'direct': False,
                    'spec': {
                        'name': 'local-system-tools_get_current_datetime',
                        'parameters': {'type': 'object', 'properties': {}},
                    },
                    'callable': tool_callable,
                }
            }
        },
    }


def test_non_streaming_completion_executes_mcp_and_returns_final_answer(monkeypatch):
    calls = []

    async def current_datetime():
        return {'date': '2026-09-11', 'timezone': 'America/Mexico_City'}

    async def finish(request, form_data, user, bypass_system_prompt=False):
        calls.append((form_data, bypass_system_prompt))
        return {
            'choices': [
                {
                    'message': {
                        'role': 'assistant',
                        'content': 'Today is September 11, 2026.',
                    }
                }
            ]
        }

    monkeypatch.setattr(middleware, 'generate_chat_completion', finish)
    ctx = make_context(current_datetime)

    result = asyncio.run(middleware.complete_non_streaming_server_tool_loop(tool_response(), ctx))

    assert result['choices'][0]['message']['content'] == 'Today is September 11, 2026.'
    assert calls[0][1] is True
    messages = calls[0][0]['messages']
    assert messages[-2]['role'] == 'assistant'
    assert messages[-2]['tool_calls'][0]['function']['name'] == 'local-system-tools_get_current_datetime'
    assert messages[-1]['role'] == 'tool'
    assert '2026-09-11' in messages[-1]['content']


def test_non_streaming_completion_stops_repeated_tool_calls(monkeypatch):
    async def current_datetime():
        return {'date': '2026-09-11'}

    async def repeat(request, form_data, user, bypass_system_prompt=False):
        return tool_response()

    monkeypatch.setattr(middleware, 'generate_chat_completion', repeat)
    ctx = make_context(current_datetime)

    with pytest.raises(HTTPException, match='repeated the same tool call'):
        asyncio.run(middleware.complete_non_streaming_server_tool_loop(tool_response(), ctx))


def test_non_streaming_completion_without_resolved_tools_is_unchanged():
    response = tool_response()
    ctx = make_context(lambda: None)
    ctx['metadata']['tools'] = {}

    result = asyncio.run(middleware.complete_non_streaming_server_tool_loop(response, ctx))

    assert result is response


def test_non_streaming_completion_reports_malformed_arguments_to_model(monkeypatch):
    continuations = []

    async def current_datetime():
        raise AssertionError('Malformed arguments must not invoke the MCP callable')

    async def finish(request, form_data, user, bypass_system_prompt=False):
        continuations.append(form_data)
        return {'choices': [{'message': {'role': 'assistant', 'content': 'Unable to read the date.'}}]}

    monkeypatch.setattr(middleware, 'generate_chat_completion', finish)
    ctx = make_context(current_datetime)

    asyncio.run(middleware.complete_non_streaming_server_tool_loop(tool_response('{bad json'), ctx))

    assert 'not a valid JSON object' in continuations[0]['messages'][-1]['content']
