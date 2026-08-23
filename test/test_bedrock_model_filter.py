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
