import asyncio

from open_webui.utils.payload import (
    apply_global_system_prompt_at_provider,
    apply_global_system_prompt_to_body,
    apply_system_prompt_to_body,
)
from open_webui.routers.openai import (
    convert_to_responses_payload,
    openai_reasoning_model_handler,
)
from open_webui.routers.users import remove_accidental_global_prompt_copy


def test_global_prompt_is_prepended_to_existing_system_prompt():
    form_data = {
        'messages': [
            {'role': 'system', 'content': 'User instruction'},
            {'role': 'user', 'content': 'Hello'},
        ]
    }

    result = asyncio.run(apply_global_system_prompt_to_body('Administrator instruction', form_data))

    assert result['messages'][0] == {
        'role': 'system',
        'content': 'Administrator instruction\nUser instruction',
    }
    assert result['messages'][1] == {'role': 'user', 'content': 'Hello'}


def test_global_prompt_creates_system_message_when_missing():
    form_data = {'messages': [{'role': 'user', 'content': 'Hello'}]}

    result = asyncio.run(apply_global_system_prompt_to_body('Administrator instruction', form_data))

    assert result['messages'][0] == {
        'role': 'system',
        'content': 'Administrator instruction',
    }


def test_global_prompt_is_idempotent_at_provider_dispatch_boundary():
    form_data = {
        'messages': [
            {
                'role': 'system',
                'content': 'Administrator instruction\nUser instruction',
            },
            {'role': 'user', 'content': 'Hello'},
        ]
    }

    result = asyncio.run(apply_global_system_prompt_to_body('Administrator instruction', form_data))

    assert result['messages'][0]['content'] == 'Administrator instruction\nUser instruction'


def test_provider_boundary_separates_admin_from_remaining_instructions():
    form_data = {
        'messages': [
            {
                'role': 'system',
                'content': 'Administrator instruction\nUser instruction\nFolder instruction',
            },
            {'role': 'user', 'content': 'Hello'},
        ]
    }

    result = asyncio.run(
        apply_global_system_prompt_at_provider('Administrator instruction', form_data)
    )

    assert result['messages'][:2] == [
        {'role': 'system', 'content': 'Administrator instruction'},
        {'role': 'system', 'content': 'User instruction\nFolder instruction'},
    ]


def test_responses_adapter_preserves_all_system_instructions_in_order():
    result = convert_to_responses_payload(
        {
            'model': 'gpt-5',
            'messages': [
                {'role': 'system', 'content': 'Administrator instruction'},
                {'role': 'system', 'content': 'User and folder instructions'},
                {'role': 'user', 'content': 'Hello'},
            ],
        }
    )

    assert result['instructions'] == (
        'Administrator instruction\n\nUser and folder instructions'
    )


def test_reasoning_adapter_converts_every_leading_system_message():
    payload = openai_reasoning_model_handler(
        {
            'model': 'gpt-5',
            'messages': [
                {'role': 'system', 'content': 'Administrator instruction'},
                {'role': 'system', 'content': 'User instruction'},
                {'role': 'user', 'content': 'Hello'},
            ],
        }
    )

    assert [message['role'] for message in payload['messages']] == [
        'developer',
        'developer',
        'user',
    ]


def test_blank_global_prompt_does_not_change_messages():
    messages = [{'role': 'user', 'content': 'Hello'}]
    form_data = {'messages': messages.copy()}

    result = asyncio.run(apply_global_system_prompt_to_body('   ', form_data))

    assert result['messages'] == messages


def test_exact_legacy_admin_prompt_copy_is_removed_from_user_settings():
    settings = {
        'ui': {
            'system': 'Administrator instruction',
            'theme': 'dark',
        }
    }

    assert remove_accidental_global_prompt_copy(settings, 'Administrator instruction') == {
        'ui': {'theme': 'dark'}
    }
    assert settings['ui']['system'] == 'Administrator instruction'


def test_independent_user_prompt_is_preserved():
    settings = {'ui': {'system': 'User instruction'}}

    assert remove_accidental_global_prompt_copy(settings, 'Administrator instruction') == settings


def test_admin_user_and_folder_prompts_have_stable_priority_order():
    form_data = {
        'messages': [
            {'role': 'system', 'content': 'User instruction'},
            {'role': 'user', 'content': 'Hello'},
        ]
    }

    form_data = asyncio.run(
        apply_system_prompt_to_body('Folder instruction', form_data, append=True)
    )
    form_data = asyncio.run(
        apply_global_system_prompt_to_body('Administrator instruction', form_data)
    )

    assert form_data['messages'][0] == {
        'role': 'system',
        'content': 'Administrator instruction\nUser instruction\nFolder instruction',
    }


def test_feature_context_follows_admin_user_and_folder_prompts():
    form_data = {
        'messages': [
            {'role': 'system', 'content': 'User instruction'},
            {'role': 'user', 'content': 'Hello'},
        ]
    }

    form_data = asyncio.run(apply_system_prompt_to_body('Folder instruction', form_data, append=True))
    form_data = asyncio.run(apply_system_prompt_to_body('Feature instruction', form_data, append=True))
    form_data = asyncio.run(apply_global_system_prompt_to_body('Administrator instruction', form_data))

    assert form_data['messages'][0]['content'] == (
        'Administrator instruction\nUser instruction\nFolder instruction\nFeature instruction'
    )


def test_reapplying_provider_model_prompt_does_not_duplicate_it():
    form_data = {
        'messages': [
            {'role': 'system', 'content': 'Administrator instruction\nModel instruction'},
            {'role': 'user', 'content': 'Hello'},
        ]
    }

    result = asyncio.run(apply_system_prompt_to_body('Model instruction', form_data, append=True))

    assert result['messages'][0]['content'] == 'Administrator instruction\nModel instruction'
