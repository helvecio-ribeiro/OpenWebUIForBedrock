from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend' / 'open_webui'


def test_legacy_function_runtime_files_are_absent():
    removed = (
        BACKEND / 'functions.py',
        BACKEND / 'models' / 'functions.py',
        BACKEND / 'routers' / 'functions.py',
        BACKEND / 'utils' / 'actions.py',
        BACKEND / 'utils' / 'filter.py',
        BACKEND / 'utils' / 'plugin.py',
    )

    assert all(not path.exists() for path in removed)


def test_main_does_not_mount_legacy_function_endpoints():
    source = (BACKEND / 'main.py').read_text()

    assert "prefix='/api/v1/functions'" not in source
    assert "@app.post('/api/chat/actions/" not in source


def test_legacy_function_request_contract_is_absent_from_runtime_sources():
    runtime_files = [
        *BACKEND.rglob('*.py'),
        *(ROOT / 'src').rglob('*.svelte'),
        *(ROOT / 'src').rglob('*.ts'),
    ]
    excluded_parts = {'migrations', 'i18n'}
    forbidden = ('ENABLE_PLUGINS', 'filter_ids', 'selectedFilterIds')

    for path in runtime_files:
        if excluded_parts.intersection(path.parts):
            continue
        source = path.read_text(errors='ignore')
        for marker in forbidden:
            assert marker not in source, f'{marker} remains in {path.relative_to(ROOT)}'
