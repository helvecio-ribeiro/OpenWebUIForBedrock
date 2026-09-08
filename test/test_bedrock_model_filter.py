import json

from open_webui.routers import bedrock


def test_text_chat_requires_modalities_and_allowlisted_id(monkeypatch):
    monkeypatch.setattr(bedrock, 'BEDROCK_CONVERSE_MODEL_PREFIXES', ('chat.',))

    assert bedrock._supports_text_chat(
        {
            'modelId': 'chat.model-v1',
            'inputModalities': ['TEXT', 'IMAGE'],
            'outputModalities': ['TEXT'],
        }
    )
    assert not bedrock._supports_text_chat(
        {
            'modelId': 'chat.embedding-v1',
            'inputModalities': ['TEXT'],
            'outputModalities': ['EMBEDDING'],
        }
    )
    assert not bedrock._supports_text_chat(
        {
            'modelId': 'unknown.model-v1',
            'inputModalities': ['TEXT'],
            'outputModalities': ['TEXT'],
        }
    )


def test_inference_profile_resolves_foundation_model_arn(monkeypatch):
    monkeypatch.setattr(bedrock, 'BEDROCK_CONVERSE_MODEL_PREFIXES', ('chat.',))
    summaries = {
        'chat.model-v1': {
            'modelId': 'chat.model-v1',
            'inputModalities': ['TEXT'],
            'outputModalities': ['TEXT'],
        }
    }
    profile = {
        'models': [
            {
                'modelArn': 'arn:aws:bedrock:us-east-1::foundation-model/chat.model-v1',
            }
        ]
    }

    assert bedrock._profile_supports_text_chat(profile, summaries)


def test_inference_profile_rejects_unknown_referenced_model(monkeypatch):
    monkeypatch.setattr(bedrock, 'BEDROCK_CONVERSE_MODEL_PREFIXES', ('chat.',))
    profile = {
        'models': [
            {
                'modelArn': 'arn:aws:bedrock:us-east-1::foundation-model/chat.unknown-v1',
            }
        ]
    }

    assert not bedrock._profile_supports_text_chat(profile, {})


def test_converse_request_translates_openai_tools_and_tool_results():
    _, request, returned_names = bedrock._converse_request(
        {
            'model': 'bedrock:amazon.nova-lite-v1:0',
            'messages': [
                {'role': 'user', 'content': 'List files'},
                {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': [
                        {
                            'id': 'call-1',
                            'type': 'function',
                            'function': {
                                'name': 'filesystem-tools_list_directory',
                                'arguments': '{"path":"."}',
                            },
                        },
                        {
                            'id': 'call-2',
                            'type': 'function',
                            'function': {'name': 'filesystem_info', 'arguments': '{}'},
                        },
                    ],
                },
                {'role': 'tool', 'tool_call_id': 'call-1', 'content': '["a.txt"]'},
                {'role': 'tool', 'tool_call_id': 'call-2', 'content': '{"root":"/home/test"}'},
            ],
            'tools': [
                {
                    'type': 'function',
                    'function': {
                        'name': 'filesystem-tools_list_directory',
                        'description': 'List files.',
                        'parameters': {
                            'type': 'object',
                            'properties': {'path': {'type': 'string'}},
                            'required': ['path'],
                            'title': 'list_directoryArguments',
                            'additionalProperties': False,
                        },
                    },
                }
            ],
        }
    )

    assert request['toolConfig']['tools'][0]['toolSpec']['name'] == 'filesystem_tools_list_directory'
    assert request['toolConfig']['toolChoice'] == {'auto': {}}
    schema = request['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json']
    assert schema == {
        'type': 'object',
        'properties': {'path': {'type': 'string'}},
        'required': ['path'],
    }
    assert request['inferenceConfig']['temperature'] == 0
    assert request['inferenceConfig']['maxTokens'] == 3000
    assert request['additionalModelRequestFields'] == {'inferenceConfig': {'topK': 1}}
    assert request['messages'][1]['content'][0]['toolUse']['name'] == 'filesystem_tools_list_directory'
    assert request['messages'][1]['content'][0]['toolUse']['input'] == {'path': '.'}
    assert returned_names == {'filesystem_tools_list_directory': 'filesystem-tools_list_directory'}
    results = request['messages'][2]['content']
    assert [block['toolResult']['toolUseId'] for block in results] == ['call-1', 'call-2']


def test_nova_greedy_tool_defaults_do_not_affect_plain_chat_or_other_models():
    _, plain_nova, _ = bedrock._converse_request(
        {'model': 'bedrock:amazon.nova-lite-v1:0', 'messages': [{'role': 'user', 'content': 'Hi'}]}
    )
    assert 'additionalModelRequestFields' not in plain_nova

    _, claude_tools, _ = bedrock._converse_request(
        {
            'model': 'bedrock:anthropic.claude-3-haiku',
            'messages': [{'role': 'user', 'content': 'Use a tool'}],
            'tools': [
                {
                    'type': 'function',
                    'function': {'name': 'demo', 'parameters': {'type': 'object'}},
                }
            ],
            'temperature': 0.7,
        }
    )
    assert claude_tools['inferenceConfig']['temperature'] == 0.7
    assert 'additionalModelRequestFields' not in claude_tools


def test_nova_does_not_force_ambiguous_calendar_delete_without_an_id_or_safe_scope():
    _, request, _ = bedrock._converse_request(
        {
            'model': 'bedrock:amazon.nova-lite-v1:0',
            'messages': [
                {'role': 'assistant', 'content': 'I found two past appointments.'},
                {'role': 'user', 'content': 'No, I said remove those appointments.'},
            ],
            'tools': [
                {
                    'type': 'function',
                    'function': {
                        'name': 'delete_calendar_event',
                        'description': 'Delete an event.',
                        'parameters': {'type': 'object', 'properties': {}},
                    },
                }
            ],
        }
    )

    assert request['toolConfig']['toolChoice'] == {'auto': {}}


def test_nova_rejects_delete_all_calendar_requests_instead_of_routing_tools():
    tools = [
        {
            'type': 'function',
            'function': {
                'name': f'local-calendar_{name}',
                'parameters': {'type': 'object', 'properties': {}},
            },
        }
        for name in ('search_calendar_events', 'delete_calendar_event', 'delete_calendar_events')
    ]
    form_data = {
        'model': 'bedrock:amazon.nova-lite-v1:0',
        'messages': [
            {'role': 'user', 'content': 'Delete all appointments in my calendar that were in the past.'}
        ],
        'tools': tools,
    }

    assert bedrock._calendar_delete_all_requested(form_data)
    _, request, _ = bedrock._converse_request(form_data)
    assert request['toolConfig']['toolChoice'] == {'auto': {}}


def test_calendar_delete_all_guard_ignores_negation_and_non_calendar_objects():
    for content in (
        'Do not delete all appointments.',
        "Don't remove every calendar event.",
        'Delete all files in this folder.',
        'List all calendar appointments.',
    ):
        assert not bedrock._calendar_delete_all_requested(
            {'messages': [{'role': 'user', 'content': content}]}
        )


def test_explicit_calendar_event_id_never_expands_to_batch_delete():
    event_id = 'b5af15df-f358-4061-ac6b-365c81d76f78'
    form_data = {
        'messages': [{'role': 'user', 'content': f'Delete the appointment {event_id}'}],
        'tools': [
            {
                'type': 'function',
                'function': {
                    'name': f'local-calendar_{name}',
                    'parameters': {'type': 'object', 'properties': {}},
                },
            }
            for name in (
                'search_calendar_events',
                'delete_calendar_event',
                'delete_calendar_events',
            )
        ],
    }

    routed_tool = bedrock._explicit_calendar_delete_tool(form_data)
    assert routed_tool == 'local-calendar_delete_calendar_event'
    assert bedrock._calendar_route_arguments(form_data, routed_tool) == {'event_id': event_id}


def test_unbounded_search_results_cannot_be_promoted_to_batch_delete():
    form_data = {
        'messages': [
            {'role': 'user', 'content': 'Delete all past appointments.'},
            {
                'role': 'assistant',
                'tool_calls': [
                    {
                        'id': 'search-1',
                        'function': {
                            'name': 'local-calendar_search_calendar_events',
                            'arguments': '{}',
                        },
                    }
                ],
            },
            {
                'role': 'tool',
                'tool_call_id': 'search-1',
                'content': '{"events":[{"id":"past"},{"id":"future"}]}',
            },
        ]
    }

    assert (
        bedrock._calendar_route_arguments(
            form_data, 'local-calendar_delete_calendar_events'
        )
        is None
    )


def test_nova_namespaced_calendar_delete_is_suppressed_after_attempt():
    form_data = {
        'model': 'bedrock:amazon.nova-lite-v1:0',
        'messages': [
            {'role': 'user', 'content': 'Delete the old calendar appointment.'},
            {
                'role': 'assistant',
                'content': None,
                'tool_calls': [
                    {
                        'id': 'delete-1',
                        'type': 'function',
                        'function': {
                            'name': 'local-calendar_delete_calendar_event',
                            'arguments': '{"event_id":"event-1"}',
                        },
                    }
                ],
            },
            {'role': 'tool', 'tool_call_id': 'delete-1', 'content': '{"status":"deleted"}'},
        ],
        'tools': [
            {
                'type': 'function',
                'function': {
                    'name': f'local-calendar_{name}',
                    'parameters': {'type': 'object', 'properties': {}},
                },
            }
            for name in ('search_calendar_events', 'delete_calendar_event')
        ],
    }

    _, request, _ = bedrock._converse_request(form_data)
    assert 'toolConfig' not in request


def test_nova_does_not_route_negated_or_informational_calendar_delete_text():
    tool = {
        'type': 'function',
        'function': {
            'name': 'delete_calendar_event',
            'parameters': {'type': 'object', 'properties': {}},
        },
    }
    for content in (
        "Do not delete my appointment.",
        'How do I delete a calendar event?',
        'Please remove that file.',
    ):
        _, request, _ = bedrock._converse_request(
            {
                'model': 'bedrock:amazon.nova-lite-v1:0',
                'messages': [{'role': 'user', 'content': content}],
                'tools': [tool],
            }
        )
        assert request['toolConfig']['toolChoice'] == {'auto': {}}


def test_nova_calendar_delete_routing_is_one_shot_and_cannot_loop():
    _, request, _ = bedrock._converse_request(
        {
            'model': 'bedrock:amazon.nova-lite-v1:0',
            'messages': [
                {'role': 'user', 'content': 'Remove those appointments.'},
                {
                    'role': 'assistant',
                    'content': None,
                    'tool_calls': [
                        {
                            'id': 'delete-1',
                            'type': 'function',
                            'function': {'name': 'delete_calendar_event', 'arguments': '{}'},
                        }
                    ],
                },
                {
                    'role': 'tool',
                    'tool_call_id': 'delete-1',
                    'content': '{"status":"event_id_required","events":[]}',
                },
            ],
            'tools': [
                {
                    'type': 'function',
                    'function': {'name': name, 'parameters': {'type': 'object', 'properties': {}}},
                }
                for name in ('search_calendar_events', 'delete_calendar_event')
            ],
        }
    )

    assert 'toolConfig' not in request


def test_nova_calendar_create_allows_one_empty_argument_retry_then_stops():
    form_data = {
        'model': 'bedrock:amazon.nova-lite-v1:0',
        'messages': [
            {'role': 'user', 'content': 'Schedule a dentist appointment tomorrow.'},
            {
                'role': 'assistant',
                'content': None,
                'tool_calls': [
                    {
                        'id': 'create-1',
                        'type': 'function',
                        'function': {'name': 'create_calendar_event', 'arguments': '{}'},
                    }
                ],
            },
            {
                'role': 'tool',
                'tool_call_id': 'create-1',
                'content': '{"status":"missing_required_fields"}',
            },
        ],
        'tools': [
            {
                'type': 'function',
                'function': {
                    'name': 'create_calendar_event',
                    'parameters': {'type': 'object', 'properties': {}},
                },
            },
            {
                'type': 'function',
                'function': {'name': 'unrelated_tool', 'parameters': {'type': 'object'}},
            },
        ],
    }

    _, retry_request, _ = bedrock._converse_request(form_data)
    assert retry_request['toolConfig']['toolChoice'] == {
        'tool': {'name': 'create_calendar_event'}
    }
    assert [tool['toolSpec']['name'] for tool in retry_request['toolConfig']['tools']] == [
        'create_calendar_event'
    ]

    form_data['messages'].extend(
        [
            {
                'role': 'assistant',
                'content': None,
                'tool_calls': [
                    {
                        'id': 'create-2',
                        'type': 'function',
                        'function': {'name': 'create_calendar_event', 'arguments': '{}'},
                    }
                ],
            },
            {
                'role': 'tool',
                'tool_call_id': 'create-2',
                'content': '{"status":"missing_required_fields"}',
            },
        ]
    )
    _, stopped_request, _ = bedrock._converse_request(form_data)
    assert [tool['toolSpec']['name'] for tool in stopped_request['toolConfig']['tools']] == [
        'unrelated_tool'
    ]


def test_nonstream_converse_translates_tool_use_content():
    calls = bedrock._tool_calls_from_content(
        [
            {
                'toolUse': {
                    'toolUseId': 'call-1',
                    'name': 'filesystem_tools_list_directory',
                    'input': {'path': '.'},
                }
            }
        ],
        {'filesystem_tools_list_directory': 'filesystem-tools_list_directory'},
    )
    call = calls[0]
    assert call['function']['name'] == 'filesystem-tools_list_directory'
    assert json.loads(call['function']['arguments']) == {'path': '.'}


def test_streaming_converse_translates_tool_call_events():
    indexes = {}
    start, finish = bedrock._stream_event_deltas(
        {
            'contentBlockStart': {
                'contentBlockIndex': 3,
                'start': {'toolUse': {'toolUseId': 'call-1', 'name': 'filesystem_tools_list_directory'}},
            }
        },
        indexes,
        {'filesystem_tools_list_directory': 'filesystem-tools_list_directory'},
    )
    arguments, _ = bedrock._stream_event_deltas(
        {
            'contentBlockDelta': {
                'contentBlockIndex': 3,
                'delta': {'toolUse': {'input': '{"path":"."}'}},
            }
        },
        indexes,
    )
    _, finish = bedrock._stream_event_deltas({'messageStop': {'stopReason': 'tool_use'}}, indexes)

    assert start[0]['tool_calls'][0]['function']['name'] == 'filesystem-tools_list_directory'
    assert arguments[0]['tool_calls'][0]['function']['arguments'] == '{"path":"."}'
    assert finish == 'tool_calls'
