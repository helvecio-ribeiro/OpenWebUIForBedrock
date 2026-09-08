import json

from open_webui.utils.middleware import append_streaming_tool_arguments


def test_streamed_tool_argument_fragments_are_accumulated():
    tool_call = {'function': {'name': 'create_calendar_event', 'arguments': ''}}

    append_streaming_tool_arguments(tool_call, '{"title":"Appointment')
    append_streaming_tool_arguments(tool_call, ' with Ana","start":"2026-09-09 12:00"}')

    assert json.loads(tool_call['function']['arguments']) == {
        'title': 'Appointment with Ana',
        'start': '2026-09-09 12:00',
    }


def test_streamed_tool_argument_object_is_serialized_and_appended():
    tool_call = {'function': {'name': 'demo', 'arguments': None}}

    append_streaming_tool_arguments(tool_call, {'value': 3})

    assert json.loads(tool_call['function']['arguments']) == {'value': 3}
