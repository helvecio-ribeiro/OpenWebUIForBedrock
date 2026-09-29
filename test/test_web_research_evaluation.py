import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'examples/managed-mcp/web-research-tools/evaluate_models.py'


def load_evaluator():
    spec = importlib.util.spec_from_file_location('web_research_model_evaluator', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def response(trace, answer='Expected marker'):
    return {
        'choices': [{'message': {'role': 'assistant', 'content': answer}}],
        'tool_trace': trace,
    }


def call(name, arguments=None, error=False):
    return {'name': f'local-web-research_{name}', 'arguments': arguments or {}, 'error': error}


def test_single_page_requires_exactly_one_fetch():
    evaluator = load_evaluator()
    case = {
        'id': 'single',
        'kind': 'single_page',
        'prompt': 'Read one page',
        'expected': ['expected marker'],
        'max_tool_calls': 1,
    }

    passed = evaluator.evaluate_response('model', case, response([call('fetch_web_page')]), 1.0)
    failed = evaluator.evaluate_response('model', case, response([call('crawl_website')]), 1.0)

    assert passed.passed is True
    assert failed.passed is False
    assert 'expected exactly one fetch_web_page' in failed.reasons[0]


def test_crawl_must_be_bounded_by_case_limits():
    evaluator = load_evaluator()
    case = {
        'id': 'crawl',
        'kind': 'bounded_crawl',
        'prompt': 'Read linked pages',
        'max_depth': 1,
        'max_pages': 5,
    }
    result = evaluator.evaluate_response(
        'model',
        case,
        response([call('crawl_website', {'max_depth': 3, 'max_pages': 12})]),
        1.0,
    )

    assert result.passed is False
    assert any('max_depth exceeded' in reason for reason in result.reasons)
    assert any('max_pages exceeded' in reason for reason in result.reasons)


def test_large_result_requires_artifact_search_or_read():
    evaluator = load_evaluator()
    case = {'id': 'large', 'kind': 'large_artifact', 'prompt': 'Find the final marker'}

    no_read = evaluator.evaluate_response(
        'model', case, response([call('fetch_web_page')]), 1.0
    )
    searched = evaluator.evaluate_response(
        'model',
        case,
        response([call('fetch_web_page'), call('search_web_artifact')]),
        1.0,
    )

    assert no_read.passed is False
    assert searched.passed is True


def test_configuration_rejects_duplicate_or_unknown_cases():
    evaluator = load_evaluator()
    with pytest.raises(ValueError, match='case kind'):
        evaluator.validate_config(
            {'models': ['model'], 'cases': [{'id': 'bad', 'kind': 'unknown', 'prompt': 'x'}]}
        )
    with pytest.raises(ValueError, match='duplicate case id'):
        evaluator.validate_config(
            {
                'models': ['model'],
                'cases': [
                    {'id': 'same', 'kind': 'single_page', 'prompt': 'x'},
                    {'id': 'same', 'kind': 'bounded_crawl', 'prompt': 'y'},
                ],
            }
        )
