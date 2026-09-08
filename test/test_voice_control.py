from open_webui.utils.middleware import get_voice_control_system_prompt


def test_exit_voice_control_has_strict_short_response():
    prompt = get_voice_control_system_prompt({'user_message': {'meta': {'voice_control': 'exit'}}})
    assert prompt is not None
    assert 'exactly "Goodbye."' in prompt
    assert 'Do not apologize' in prompt


def test_unknown_or_missing_voice_control_has_no_prompt():
    assert get_voice_control_system_prompt({}) is None
    assert get_voice_control_system_prompt({'user_message': {'meta': {'voice_control': 'unsupported'}}}) is None
