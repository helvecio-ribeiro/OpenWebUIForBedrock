import pytest

from open_webui.utils.tts import (
    detect_tts_language,
    normalize_tts_language,
    resolve_tts_voice,
    tts_cache_key,
)


VOICES = {'en': 'bf_emma', 'es': 'ef_dora', 'pt': 'pf_dora'}


@pytest.mark.parametrize(
    ('locale', 'expected'),
    [
        ('en-US', 'en'),
        ('es_MX', 'es'),
        ('ES', 'es'),
        ('pt-BR', 'pt'),
        ('pt_PT', 'pt'),
        ('fr-FR', None),
        (None, None),
    ],
)
def test_normalize_tts_language(locale, expected):
    assert normalize_tts_language(locale) == expected


def test_detects_english_and_spanish_from_complete_responses():
    assert detect_tts_language('Your appointment is tomorrow at the main office.') == 'en'
    assert detect_tts_language('Su cita es mañana en la oficina principal.') == 'es'


def test_detects_brazilian_portuguese_without_confusing_it_with_spanish():
    assert detect_tts_language('Olá, sua consulta está marcada para amanhã.') == 'pt'
    assert detect_tts_language('Você não tem compromissos no calendário hoje.') == 'pt'
    assert resolve_tts_voice(
        text='A reunião foi confirmada para amanhã.',
        voices=VOICES,
        preferred_language='en-US',
    ) == ('pt', 'pf_dora')


def test_accumulated_context_keeps_ambiguous_portuguese_fragment_on_portuguese_voice():
    ambiguous_fragment = (
        'Se tiver alguma pergunta ou precisar de assistência, '
        'sinta-se à vontade para perguntar.'
    )
    assert resolve_tts_voice(
        text=ambiguous_fragment,
        voices=VOICES,
        preferred_language='en-US',
    ) == ('en', 'bf_emma')
    assert resolve_tts_voice(
        text=(
            'Claro, posso ajudar em português do Brasil. '
            f'{ambiguous_fragment}'
        ),
        voices=VOICES,
        preferred_language='en-US',
    ) == ('pt', 'pf_dora')


def test_ambiguous_short_text_uses_browser_preference():
    assert resolve_tts_voice(text='OK.', voices=VOICES, preferred_language='es-MX') == (
        'es',
        'ef_dora',
    )


def test_detected_response_language_wins_over_browser_preference():
    assert resolve_tts_voice(
        text='This response is written in English for the user.',
        voices=VOICES,
        preferred_language='es-MX',
    ) == ('en', 'bf_emma')


def test_explicit_language_wins_over_detection():
    assert resolve_tts_voice(
        text='This response is written in English.',
        voices=VOICES,
        explicit_language='es',
        preferred_language='en',
    ) == ('es', 'ef_dora')


def test_missing_default_language_falls_back_to_an_available_voice():
    assert resolve_tts_voice(text='OK.', voices={'es': 'ef_dora'}, default_language='en') == (
        'es',
        'ef_dora',
    )


def test_requires_at_least_one_supported_voice():
    with pytest.raises(ValueError, match='At least one supported TTS language voice'):
        resolve_tts_voice(text='Hello', voices={'fr': 'ff_siwis'})


def test_cache_key_separates_resolved_voices():
    english = {'model': 'kokoro', 'voice': 'bf_emma', 'input': 'OK.'}
    spanish = {**english, 'voice': 'ef_dora'}
    assert tts_cache_key('openai', english) != tts_cache_key('openai', spanish)
