import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

from open_webui.env import AWS_CREDENTIALS, AWS_REGION, BEDROCK_CONVERSE_MODEL_PREFIXES
from open_webui.models.config import Config
from open_webui.utils.payload import resolve_system_prompt

log = logging.getLogger(__name__)


def _aws_credentials():
    return {
        key: value
        for key, value in AWS_CREDENTIALS.items()
        if value
    }

def _list_foundation_models() -> list[dict]:
    session = boto3.Session(region_name=AWS_REGION, **_aws_credentials())
    client = session.client('bedrock')
    response = client.list_foundation_models()
    return response.get('modelSummaries', [])


def _list_inference_profiles() -> list[dict]:
    session = boto3.Session(region_name=AWS_REGION, **_aws_credentials())
    client = session.client('bedrock')
    profiles = []
    next_token = None
    while True:
        request = {'nextToken': next_token} if next_token else {}
        response = client.list_inference_profiles(**request)
        profiles.extend(response.get('inferenceProfileSummaries', []))
        next_token = response.get('nextToken')
        if not next_token:
            return profiles


def _foundation_model_id(model: dict) -> str:
    model_id = model.get('modelId')
    if model_id:
        return model_id

    model_arn = model.get('modelArn', '')
    marker = 'foundation-model/'
    return model_arn.split(marker, 1)[1] if marker in model_arn else ''


def _supports_converse(model_id: str) -> bool:
    return bool(model_id) and model_id.startswith(BEDROCK_CONVERSE_MODEL_PREFIXES)


def _supports_text_chat(summary: dict) -> bool:
    return (
        _supports_converse(_foundation_model_id(summary))
        and 'TEXT' in summary.get('inputModalities', [])
        and 'TEXT' in summary.get('outputModalities', [])
    )


def _profile_supports_text_chat(profile: dict, summaries_by_id: dict[str, dict]) -> bool:
    return any(
        _supports_text_chat(summaries_by_id[model_id])
        for model in profile.get('models', [])
        if (model_id := _foundation_model_id(model)) in summaries_by_id
    )


def _bedrock_model_id(model_id: str) -> str:
    return model_id.removeprefix('bedrock:')


def _text_blocks(content) -> list[dict]:
    if isinstance(content, list):
        return [
            {'text': str(item.get('text', ''))}
            for item in content
            if isinstance(item, dict) and item.get('type') == 'text' and item.get('text') is not None
        ]
    return [{'text': str(content)}] if content is not None else []


def _converse_messages(messages: list[dict], tool_names: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    tool_names = tool_names or {}
    system = []
    converted = []
    for message in messages:
        role = message.get('role', 'user')
        if role == 'system':
            system.extend(_text_blocks(message.get('content', '')))
            continue

        if role == 'tool':
            blocks = [
                {
                    'toolResult': {
                        'toolUseId': message.get('tool_call_id', ''),
                        'content': _text_blocks(message.get('content', '')) or [{'text': ''}],
                    }
                }
            ]
            # Bedrock expects results for parallel calls in one user turn.
            if converted and converted[-1]['role'] == 'user' and all(
                'toolResult' in block for block in converted[-1]['content']
            ):
                converted[-1]['content'].extend(blocks)
            else:
                converted.append({'role': 'user', 'content': blocks})
            continue

        blocks = _text_blocks(message.get('content', ''))
        if role == 'assistant':
            blocks.extend(
                {
                    'toolUse': {
                        'toolUseId': tool_call.get('id', ''),
                        'name': tool_names.get(
                            (tool_call.get('function') or {}).get('name', ''),
                            (tool_call.get('function') or {}).get('name', ''),
                        ),
                        'input': _json_arguments((tool_call.get('function') or {}).get('arguments', '{}')),
                    }
                }
                for tool_call in message.get('tool_calls') or []
            )
        converted.append({'role': role if role in ('user', 'assistant') else 'user', 'content': blocks})
    return system, converted


def _json_arguments(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or '{}')
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nova_tool_schema(schema: dict) -> dict:
    """Restrict the schema root to the subset accepted by Amazon Nova."""
    normalized = {
        'type': 'object',
        'properties': schema.get('properties') if isinstance(schema.get('properties'), dict) else {},
    }
    if isinstance(schema.get('required'), list) and schema['required']:
        normalized['required'] = schema['required']
    return normalized


def _bedrock_tool_name_maps(form_data: dict) -> tuple[dict[str, str], dict[str, str]]:
    forward = {}
    reverse = {}
    for tool in form_data.get('tools') or []:
        original = (tool.get('function') or {}).get('name')
        if not original:
            continue
        safe = re.sub(r'[^a-zA-Z0-9_]', '_', original)[:64]
        if safe in reverse and reverse[safe] != original:
            suffix = hashlib.sha256(original.encode()).hexdigest()[:8]
            safe = f'{safe[:55]}_{suffix}'
        forward[original] = safe
        reverse[safe] = original
    return forward, reverse


def _calendar_tool_name(form_data: dict, operation: str) -> str | None:
    """Return a native or server-namespaced calendar tool name."""
    return next(
        (
            name
            for tool in form_data.get('tools') or []
            if (name := (tool.get('function') or {}).get('name'))
            and (name == operation or name.endswith(f'_{operation}'))
        ),
        None,
    )


def _is_calendar_tool(name: str | None, operation: str) -> bool:
    return bool(name) and (name == operation or name.endswith(f'_{operation}'))


def _calendar_delete_all_requested(form_data: dict) -> bool:
    """Detect an explicit request to delete every calendar event."""
    messages = form_data.get('messages') or []
    latest_user = next(
        (message for message in reversed(messages) if message.get('role') == 'user'),
        None,
    )
    if not latest_user:
        return False
    text = ' '.join(block.get('text', '') for block in _text_blocks(latest_user.get('content')))
    normalized = re.sub(r'\s+', ' ', text.lower()).strip()
    if re.search(r"\b(do not|don't|dont|not)\s+(delete|remove|cancel)\b", normalized):
        return False
    return bool(
        re.search(r'\b(delete|remove|cancel)\b', normalized)
        and re.search(r'\b(all|every)\b', normalized)
        and re.search(r'\b(appointment|event|calendar entry|reminder)s?\b', normalized)
    )


def _explicit_calendar_delete_tool(form_data: dict) -> str | None:
    """Route only an exact-ID calendar deletion; never widen its scope."""
    single_delete_tool = _calendar_tool_name(form_data, 'delete_calendar_event')
    if not single_delete_tool:
        return None

    messages = form_data.get('messages') or []
    latest_user_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].get('role') == 'user'),
        None,
    )
    if latest_user_index is None:
        return None

    attempted_tools = {
        (call.get('function') or {}).get('name')
        for message in messages[latest_user_index + 1 :]
        if message.get('role') == 'assistant'
        for call in message.get('tool_calls') or []
    }
    # Once deletion was attempted, let the model summarize. Forcing it again
    # creates an unbounded tool loop when a tool rejects an ID.
    if any(
        _is_calendar_tool(name, operation)
        for name in attempted_tools
        for operation in ('delete_calendar_event', 'delete_calendar_events')
    ):
        return None

    latest_text = ' '.join(block.get('text', '') for block in _text_blocks(messages[latest_user_index].get('content')))
    normalized = re.sub(r'\s+', ' ', latest_text.lower()).strip()
    if not re.search(r'\b(delete|remove|cancel)\b', normalized):
        return None
    if re.search(r"\b(do not|don't|dont|not)\s+(delete|remove|cancel)\b", normalized):
        return None
    if re.search(
        r'\b(?:how\s+(?:do|can|could|would)|can|could|would)\s+(?:i|you)\s+(?:delete|remove|cancel)\b',
        normalized,
    ):
        return None

    context = ' '.join(
        ' '.join(block.get('text', '') for block in _text_blocks(message.get('content')))
        for message in messages[max(0, latest_user_index - 6) : latest_user_index + 1]
    ).lower()
    if not re.search(r'\b(appointment|event|calendar|reminder)s?\b', context):
        return None

    if re.search(
        r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
        normalized,
        re.IGNORECASE,
    ):
        return single_delete_tool
    return None


def _calendar_tools_suppressed_after_mutation_attempt(form_data: dict) -> set[str]:
    """Stop Nova from repeating calendar mutations within one user turn."""
    messages = form_data.get('messages') or []
    latest_user_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].get('role') == 'user'),
        None,
    )
    if latest_user_index is None:
        return set()
    attempted_calls = [
        call
        for message in messages[latest_user_index + 1 :]
        if message.get('role') == 'assistant'
        for call in message.get('tool_calls') or []
    ]
    attempted_tools = {
        (call.get('function') or {}).get('name') for call in attempted_calls
    }
    suppressed = set()
    for name in attempted_tools:
        if _is_calendar_tool(name, 'create_calendar_event'):
            create_calls = [
                call
                for call in attempted_calls
                if _is_calendar_tool(
                    (call.get('function') or {}).get('name'), 'create_calendar_event'
                )
            ]
            # Nova occasionally emits an empty input object even though the
            # required values are present in the prompt. Permit one corrective
            # retry after that validation error, but suppress a populated call
            # immediately and cap empty calls at two to prevent a tool loop.
            if len(create_calls) >= 2 or any(
                _json_arguments((call.get('function') or {}).get('arguments'))
                for call in create_calls
            ):
                suppressed.add(name)
        if any(
            _is_calendar_tool(name, operation)
            for operation in ('delete_calendar_event', 'delete_calendar_events')
        ):
            for operation in (
                'search_calendar_events',
                'delete_calendar_event',
                'delete_calendar_events',
            ):
                if tool_name := _calendar_tool_name(form_data, operation):
                    suppressed.add(tool_name)
    return suppressed


def _empty_calendar_create_retry_tool(form_data: dict) -> str | None:
    """Force one retry when Nova's first calendar-create call has empty arguments."""
    create_tool = _calendar_tool_name(form_data, 'create_calendar_event')
    if not create_tool:
        return None
    messages = form_data.get('messages') or []
    latest_user_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].get('role') == 'user'),
        None,
    )
    if latest_user_index is None:
        return None
    calls = [
        call
        for message in messages[latest_user_index + 1 :]
        if message.get('role') == 'assistant'
        for call in message.get('tool_calls') or []
        if _is_calendar_tool(
            (call.get('function') or {}).get('name'), 'create_calendar_event'
        )
    ]
    if len(calls) == 1 and not _json_arguments((calls[0].get('function') or {}).get('arguments')):
        return create_tool
    return None


def _calendar_route_arguments(form_data: dict, tool_name: str) -> dict | None:
    """Build the exact argument for a safe single-event deletion."""
    messages = form_data.get('messages') or []
    latest_user_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].get('role') == 'user'),
        None,
    )
    if latest_user_index is None:
        return None

    latest_text = ' '.join(
        block.get('text', '') for block in _text_blocks(messages[latest_user_index].get('content'))
    ).lower()
    if _is_calendar_tool(tool_name, 'delete_calendar_event'):
        event_id = re.search(
            r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
            latest_text,
            re.IGNORECASE,
        )
        return {'event_id': event_id.group(0)} if event_id else None

    return None


def _converse_tool_config(
    form_data: dict, tool_names: dict[str, str], excluded_tools: set[str] | None = None
) -> dict | None:
    excluded_tools = excluded_tools or set()
    tools = []
    for tool in form_data.get('tools') or []:
        function = tool.get('function') or {}
        name = function.get('name')
        if not name or name in excluded_tools:
            continue
        parameters = function.get('parameters') or {'type': 'object', 'properties': {}}
        spec = {
            'name': tool_names.get(name, name),
            'inputSchema': {'json': _nova_tool_schema(parameters)},
        }
        if function.get('description'):
            spec['description'] = function['description']
        tools.append({'toolSpec': spec})
    if not tools:
        return None

    config = {'tools': tools}
    choice = form_data.get('tool_choice')
    if choice == 'required':
        config['toolChoice'] = {'any': {}}
    elif isinstance(choice, dict) and (choice.get('function') or {}).get('name'):
        name = choice['function']['name']
        config['toolChoice'] = {'tool': {'name': tool_names.get(name, name)}}
    else:
        config['toolChoice'] = {'auto': {}}
    return config


def _converse_request(form_data: dict) -> tuple[object, dict, dict[str, str]]:
    model_id = _bedrock_model_id(form_data['model'])
    tool_names, returned_tool_names = _bedrock_tool_name_maps(form_data)
    system, messages = _converse_messages(form_data.get('messages', []), tool_names)
    inference_config = {}
    params = form_data.get('params') or {}
    for source, target in (
        ('max_tokens', 'maxTokens'),
        ('temperature', 'temperature'),
        ('top_p', 'topP'),
        ('stop', 'stopSequences'),
    ):
        value = form_data.get(source, params.get(source))
        if value is not None:
            inference_config[target] = value

    request = {
        'modelId': model_id,
        'messages': messages,
    }
    if system:
        request['system'] = system
    if inference_config:
        request['inferenceConfig'] = inference_config
    excluded_tools = (
        _calendar_tools_suppressed_after_mutation_attempt(form_data)
        if 'amazon.nova' in model_id
        else set()
    )
    if tool_config := _converse_tool_config(form_data, tool_names, excluded_tools):
        request['toolConfig'] = tool_config
        if 'amazon.nova' in model_id:
            # Nova's documented mitigation for malformed ToolUse sequences is
            # greedy decoding. Keep this scoped to tool-enabled Nova requests.
            inference_config['temperature'] = 0
            inference_config.setdefault('maxTokens', 3000)
            request['inferenceConfig'] = inference_config
            request['additionalModelRequestFields'] = {'inferenceConfig': {'topK': 1}}
            create_retry_tool = _empty_calendar_create_retry_tool(form_data)
            if forced_tool := (_explicit_calendar_delete_tool(form_data) or create_retry_tool):
                safe_forced_name = tool_names.get(forced_tool, forced_tool)
                request['toolConfig']['toolChoice'] = {
                    'tool': {'name': safe_forced_name}
                }
                if create_retry_tool:
                    # A forced choice alone still sends Nova every schema. Its
                    # argument generation becomes unreliable with the full
                    # Open WebUI tool catalog, so make the corrective retry a
                    # genuinely focused one-tool request.
                    request['toolConfig']['tools'] = [
                        tool
                        for tool in request['toolConfig']['tools']
                        if tool.get('toolSpec', {}).get('name') == safe_forced_name
                    ]
    return model_id, request, returned_tool_names


def _prepend_global_system_block(payload: dict, global_system_prompt: str) -> None:
    """Put the admin prompt in its own first AWS Converse system block."""
    global_system_prompt = str(global_system_prompt or '').strip()
    if not global_system_prompt:
        return

    remaining = []
    prefix_pending = True
    for block in payload.get('system', []):
        text = str(block.get('text', ''))
        if prefix_pending:
            if text == global_system_prompt:
                text = ''
            elif text.startswith(f'{global_system_prompt}\n'):
                text = text[len(global_system_prompt) :].lstrip()
            prefix_pending = False
        if text:
            remaining.append({'text': text})
    payload['system'] = [{'text': global_system_prompt}, *remaining]


def _openai_response(
    model_id: str,
    content: str,
    usage: dict | None = None,
    tool_calls: list[dict] | None = None,
) -> dict:
    message = {'role': 'assistant', 'content': content or None}
    if tool_calls:
        message['tool_calls'] = tool_calls
    response = {
        'id': f'chatcmpl-{uuid.uuid4().hex}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': f'bedrock:{model_id}',
        'choices': [
            {
                'index': 0,
                'message': message,
                'finish_reason': 'tool_calls' if tool_calls else 'stop',
            }
        ],
    }
    if usage:
        response['usage'] = {
            'prompt_tokens': usage.get('inputTokens', 0),
            'completion_tokens': usage.get('outputTokens', 0),
            'total_tokens': usage.get('totalTokens', 0),
        }
    return response


def _tool_calls_from_content(
    content_blocks: list[dict], returned_tool_names: dict[str, str] | None = None
) -> list[dict]:
    returned_tool_names = returned_tool_names or {}
    return [
        {
            'id': block['toolUse']['toolUseId'],
            'type': 'function',
            'function': {
                'name': returned_tool_names.get(block['toolUse']['name'], block['toolUse']['name']),
                'arguments': json.dumps(block['toolUse'].get('input') or {}),
            },
        }
        for block in content_blocks
        if block.get('toolUse')
    ]


def _openai_chunk(completion_id: str, model_id: str, delta: dict, finish_reason=None) -> str:
    return (
        f'data: {json.dumps({"id": completion_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": f"bedrock:{model_id}", "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]})}\n\n'
    )


def _stream_event_deltas(
    event: dict,
    tool_indexes: dict[int, int],
    returned_tool_names: dict[str, str] | None = None,
) -> tuple[list[dict], str | None]:
    returned_tool_names = returned_tool_names or {}
    deltas = []
    text = event.get('contentBlockDelta', {}).get('delta', {}).get('text')
    if text:
        deltas.append({'content': text})

    block_start = event.get('contentBlockStart', {})
    tool_start = block_start.get('start', {}).get('toolUse')
    if tool_start:
        block_index = block_start.get('contentBlockIndex', len(tool_indexes))
        tool_index = len(tool_indexes)
        tool_indexes[block_index] = tool_index
        deltas.append(
            {
                'tool_calls': [
                    {
                        'index': tool_index,
                        'id': tool_start['toolUseId'],
                        'type': 'function',
                        'function': {
                            'name': returned_tool_names.get(tool_start['name'], tool_start['name']),
                            'arguments': '',
                        },
                    }
                ]
            }
        )

    block_delta = event.get('contentBlockDelta', {})
    tool_delta = block_delta.get('delta', {}).get('toolUse')
    if tool_delta and block_delta.get('contentBlockIndex') in tool_indexes:
        deltas.append(
            {
                'tool_calls': [
                    {
                        'index': tool_indexes[block_delta['contentBlockIndex']],
                        'function': {'arguments': tool_delta.get('input', '')},
                    }
                ]
            }
        )

    stop_reason = event.get('messageStop', {}).get('stopReason')
    finish_reason = ('tool_calls' if stop_reason == 'tool_use' else 'stop') if stop_reason else None
    return deltas, finish_reason


async def generate_chat_completion(request: Request, form_data: dict, user=None):
    # Build the AWS system array explicitly for every invocation. Keep the
    # administrator prompt as the first, independent system block.
    metadata = form_data.get('metadata') or getattr(request.state, 'metadata', {}) or {}
    global_system_prompt = await resolve_system_prompt(
        await Config.get('prompts.global_system', ''),
        metadata,
        user,
    )
    model_id, payload, returned_tool_names = _converse_request(form_data)
    normalized_global_prompt = str(global_system_prompt or '').strip()
    _prepend_global_system_block(payload, normalized_global_prompt)
    system_text = '\n'.join(
        block.get('text', '') for block in payload.get('system', []) if block.get('text')
    )
    log.info(
        'Bedrock instruction context: model=%s system_blocks=%d system_chars=%d '
        'global_chars=%d global_is_prefix=%s system_sha256=%s',
        model_id,
        len(payload.get('system', [])),
        len(system_text),
        len(normalized_global_prompt),
        bool(normalized_global_prompt and system_text.startswith(normalized_global_prompt)),
        hashlib.sha256(system_text.encode()).hexdigest()[:12] if system_text else 'none',
    )
    if _calendar_delete_all_requested(form_data):
        refusal = (
            'Bulk deletion of all calendar appointments is disabled. '
            'Delete appointments individually using their exact event IDs.'
        )
        if not form_data.get('stream'):
            return JSONResponse(_openai_response(model_id, refusal))

        async def refusal_stream() -> AsyncIterator[str]:
            completion_id = f'chatcmpl-{uuid.uuid4().hex}'
            yield _openai_chunk(completion_id, model_id, {'content': refusal})
            yield _openai_chunk(completion_id, model_id, {}, 'stop')
            yield 'data: [DONE]\n\n'

        return StreamingResponse(refusal_stream(), media_type='text/event-stream')

    routed_tool = (
        _explicit_calendar_delete_tool(form_data) if 'amazon.nova' in model_id else None
    )
    routed_arguments = (
        _calendar_route_arguments(form_data, routed_tool) if routed_tool else None
    )
    if routed_tool and routed_arguments is not None:
        tool_call = {
            'id': f'tooluse_{uuid.uuid4().hex}',
            'type': 'function',
            'function': {
                'name': routed_tool,
                'arguments': json.dumps(routed_arguments),
            },
        }
        if not form_data.get('stream'):
            return JSONResponse(_openai_response(model_id, '', tool_calls=[tool_call]))

        async def routed_stream() -> AsyncIterator[str]:
            completion_id = f'chatcmpl-{uuid.uuid4().hex}'
            yield _openai_chunk(
                completion_id,
                model_id,
                {'tool_calls': [{**tool_call, 'index': 0}]},
            )
            yield _openai_chunk(completion_id, model_id, {}, 'tool_calls')
            yield 'data: [DONE]\n\n'

        return StreamingResponse(routed_stream(), media_type='text/event-stream')

    session = boto3.Session(region_name=AWS_REGION, **_aws_credentials())
    client = session.client('bedrock-runtime')

    if not form_data.get('stream'):
        response = await asyncio.to_thread(client.converse, **payload)
        output = response.get('output', {}).get('message', {}).get('content', [])
        content = ''.join(block.get('text', '') for block in output if block.get('text'))
        tool_calls = _tool_calls_from_content(output, returned_tool_names)
        return JSONResponse(_openai_response(model_id, content, response.get('usage'), tool_calls))

    async def stream() -> AsyncIterator[str]:
        completion_id = f'chatcmpl-{uuid.uuid4().hex}'
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        sentinel = object()

        def read_stream():
            try:
                for event in client.converse_stream(**payload).get('stream', []):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        worker = asyncio.create_task(asyncio.to_thread(read_stream))
        tool_indexes = {}
        finish_reason = 'stop'
        while True:
            event = await queue.get()
            if event is sentinel:
                break
            deltas, event_finish_reason = _stream_event_deltas(event, tool_indexes, returned_tool_names)
            for delta in deltas:
                yield _openai_chunk(completion_id, model_id, delta)
            if event_finish_reason:
                finish_reason = event_finish_reason
        await worker
        yield _openai_chunk(completion_id, model_id, {}, finish_reason)
        yield 'data: [DONE]\n\n'

    return StreamingResponse(stream(), media_type='text/event-stream')


async def get_all_models(request: Request, user=None) -> list[dict]:
    del request, user

    try:
        summaries = await asyncio.to_thread(_list_foundation_models)
    except (BotoCoreError, ClientError) as exc:
        log.warning('Unable to list AWS Bedrock models: %s', exc)
        return []

    try:
        profiles = await asyncio.to_thread(_list_inference_profiles)
    except (BotoCoreError, ClientError) as exc:
        log.warning('Unable to list AWS Bedrock inference profiles: %s', exc)
        profiles = []

    models = []
    summaries_by_id = {
        model_id: summary
        for summary in summaries
        if (model_id := _foundation_model_id(summary))
    }
    for summary in summaries:
        model_id = summary.get('modelId')
        if not model_id:
            continue

        # ListFoundationModels exposes modalities but not Converse API
        # compatibility. Keep only text-in/text-out models whose IDs match the
        # configurable Converse allowlist.
        if not _supports_text_chat(summary):
            continue

        # Models without on-demand support must be invoked through an
        # inference profile rather than their foundation model ID.
        if 'ON_DEMAND' not in summary.get('inferenceTypesSupported', ['ON_DEMAND']):
            continue

        model_name = summary.get('modelName') or model_id
        models.append(
            {
                'id': f'bedrock:{model_id}',
                'name': model_name,
                'object': 'model',
                'created': 0,
                'owned_by': 'bedrock',
                'provider': 'AWS',
                'bedrock': {
                    'model_id': model_id,
                    'provider_name': summary.get('providerName'),
                    'input_modalities': summary.get('inputModalities', []),
                    'output_modalities': summary.get('outputModalities', []),
                    'response_streaming_supported': summary.get('responseStreamingSupported', False),
                },
                'connection_type': 'external',
                'tags': [{'name': 'AWS'}],
            }
        )

    for profile in profiles:
        profile_id = profile.get('inferenceProfileId')
        if not profile_id or not _profile_supports_text_chat(profile, summaries_by_id):
            continue

        profile_name = profile.get('inferenceProfileName') or profile_id
        models.append(
            {
                'id': f'bedrock:{profile_id}',
                'name': profile_name,
                'object': 'model',
                'created': 0,
                'owned_by': 'bedrock',
                'provider': 'AWS',
                'bedrock': {
                    'inference_profile_id': profile_id,
                    'profile_type': profile.get('type'),
                    'models': profile.get('models', []),
                },
                'connection_type': 'external',
                'tags': [{'name': 'AWS'}, {'name': 'inference-profile'}],
            }
        )

    return models
