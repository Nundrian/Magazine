"""Actual C2.3 UI bodies through unchanged local HTTP admission. No live access."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml
import magazine_ef as runner
from test_magazine_library import client, library, A, B, inventory


@pytest.fixture
def tool_capture(monkeypatch, library):
    data = json.loads((Path(__file__).parent / 'fixtures/c23-tool-requests.json').read_bytes())
    assert data['source_sha256'] == hashlib.sha256(Path(runner.__file__).read_bytes()).hexdigest()
    # The UI now carries a verified library reference for export labels. Seed
    # the formerly synthetic catalogue preview so the real loader can bind it.
    project = library / 'Demo'
    (project / 'cartridges').mkdir(parents=True)
    manifest = {'schema_version': '1.0', 'name': 'Tool selection test', 'cartridges': []}
    for index, cartridge in enumerate(data['validated_document']['cartridges']):
        prompt_file = f'cartridges/{index}.md'
        (project / prompt_file).write_text(cartridge['prompt'], encoding='utf-8')
        manifest['cartridges'].append({'id': cartridge['id'], 'model': cartridge.get('job_type', cartridge['id']), 'prompt_file': prompt_file})
    (project / 'magazine.yaml').write_text(yaml.safe_dump(manifest), encoding='utf-8')
    async def models(app, user):
        return [{'id':'mock-first'}, {'id':'mock-second'}] if user.id == 'test-a' else []
    async def tools(app, user):
        return data['tools'] if user.id == 'test-a' else []
    monkeypatch.setattr(runner, '_fetch_models', models)
    monkeypatch.setattr(runner, '_fetch_tools', tools)
    return data


@pytest.mark.parametrize('mode', ['default','excluded','cleared','reselected'])
def test_c23_exact_interface_tool_ids_admit_and_persist_without_starting(client, tool_capture, mode):
    body = copy.deepcopy(tool_capture['requests'][mode])
    response = client.post('/api/magazine/runs', headers=A, json=body)
    assert response.status_code == 200, response.text
    ready = response.json(); rid = ready['run_id']
    expected = ['tool-a','tool-c'] if mode == 'excluded' else [] if mode == 'cleared' else ['tool-a','tool-b','tool-c']
    assert ready['status'] == 'READY' and ready['tool_ids'] == expected
    assert ready['model_map'] == {'first':'mock-first','second':'mock-second'}
    assert runner._load_meta('test-a',rid)['tool_ids'] == expected
    assert runner._status_payload('test-a',rid)['execution'] is None
    stored = json.loads(Path(runner._mag_path('test-a',rid)).read_bytes())
    assert stored == json.loads(body['magazine'])
    assert [c['prompt'] for c in stored['cartridges']] == [c['prompt'] for c in tool_capture['validated_document']['cartridges']]
    before = inventory(Path(runner._run_base('test-a',rid)))
    assert client.get('/api/magazine/runs/'+rid, headers=B).status_code == 404
    assert client.get('/api/magazine/runs/'+rid+'/results', headers=B).status_code == 404
    assert client.get('/api/magazine/runs', headers=B).json()['runs'] == []
    assert inventory(Path(runner._run_base('test-a',rid))) == before


def test_c23_existing_tool_admission_still_refuses_unavailable_ids(client, tool_capture):
    body = copy.deepcopy(tool_capture['requests']['default'])
    body['tool_ids'].append('not-admitted')
    assert client.post('/api/magazine/runs', headers=A, json=body).status_code == 400
    assert client.get('/api/magazine/runs', headers=A).json()['runs'] == []


def test_tool_selection_contract_remains_server_persisted():
    fields = set(runner._CreateRunBody.model_fields)
    assert 'tool_ids' in fields
    source = __import__('inspect').getsource(runner._install_routes)
    assert 'tool_ids = body.tool_ids or []' in source
    assert '"tool_ids": tool_ids' in source
