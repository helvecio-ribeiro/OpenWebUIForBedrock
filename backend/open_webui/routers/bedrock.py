import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

from open_webui.env import AWS_CREDENTIALS, AWS_REGION

log = logging.getLogger(__name__)


def _list_foundation_models() -> list[dict]:
    session = boto3.Session(region_name=AWS_REGION, **AWS_CREDENTIALS)
    client = session.client('bedrock')
    response = client.list_foundation_models()
    return response.get('modelSummaries', [])


def _list_inference_profiles() -> list[dict]:
    session = boto3.Session(region_name=AWS_REGION, **AWS_CREDENTIALS)
    client = session.client('bedrock')
    response = client.list_inference_profiles()
    return response.get('inferenceProfileSummaries', [])


def _bedrock_model_id(model_id: str) -> str:
    return model_id.removeprefix('bedrock:')


def _converse_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    system = []
    converted = []
    for message in messages:
        role = message.get('role', 'user')
        content = message.get('content', '')
        if isinstance(content, list):
            content = ''.join(
                item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') == 'text'
            )
        blocks = [{'text': str(content)}]
        if role == 'system':
            system.extend(blocks)
        else:
            converted.append({'role': role if role in ('user', 'assistant') else 'user', 'content': blocks})
    return system, converted


def _converse_request(form_data: dict) -> tuple[object, dict]:
    model_id = _bedrock_model_id(form_data['model'])
    system, messages = _converse_messages(form_data.get('messages', []))
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
    return model_id, request


def _openai_response(model_id: str, content: str, usage: dict | None = None) -> dict:
    response = {
        'id': f'chatcmpl-{uuid.uuid4().hex}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': f'bedrock:{model_id}',
        'choices': [
            {
                'index': 0,
                'message': {'role': 'assistant', 'content': content},
                'finish_reason': 'stop',
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


def _openai_chunk(completion_id: str, model_id: str, delta: dict, finish_reason=None) -> str:
    return (
        f'data: {json.dumps({"id": completion_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": f"bedrock:{model_id}", "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]})}\n\n'
    )


async def generate_chat_completion(request: Request, form_data: dict, user=None):
    del request, user
    model_id, payload = _converse_request(form_data)
    session = boto3.Session(region_name=AWS_REGION, **AWS_CREDENTIALS)
    client = session.client('bedrock-runtime')

    if not form_data.get('stream'):
        response = await asyncio.to_thread(client.converse, **payload)
        output = response.get('output', {}).get('message', {}).get('content', [])
        content = ''.join(block.get('text', '') for block in output if block.get('text'))
        return JSONResponse(_openai_response(model_id, content, response.get('usage')))

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
        while True:
            event = await queue.get()
            if event is sentinel:
                break
            delta = event.get('contentBlockDelta', {}).get('delta', {}).get('text')
            if delta:
                yield _openai_chunk(completion_id, model_id, {'content': delta})
        await worker
        yield _openai_chunk(completion_id, model_id, {}, 'stop')
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
    for summary in summaries:
        model_id = summary.get('modelId')
        if not model_id:
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
        if not profile_id:
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