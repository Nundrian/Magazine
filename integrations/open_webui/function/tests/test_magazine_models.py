"""C2.1 actual UI payload -> existing HTTP admission -> persisted core dispatch.

Run only in an isolated Linux test environment, never the live OWUI container.
The Node capture is regenerated before testing and bound to the Function hash.
"""
import asyncio
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import types
import zipfile

import pytest
import magazine_ef as runner
from test_magazine_library import client, library, A, B, inventory

@pytest.fixture
def captured():
    data = json.loads((Path(__file__).parent / 'fixtures/c21-ui-requests.json').read_text())
    assert data['source_sha256'] == hashlib.sha256(Path(runner.__file__).read_bytes()).hexdigest(), 'Regenerate Node capture before testing'
    return data


def test_release_admission_contract_excludes_client_owned_identity():
    # beta.5 adds only a server-verified catalogue reference for export labels.
    # It is not an ownership or permission field (see storage mismatch tests).
    assert set(runner._CreateRunBody.model_fields) == {'magazine', 'model_map', 'tool_ids', 'project_id'}
    assert 'owner_id = str(user.id)' in __import__('inspect').getsource(runner._install_routes)


def test_model_discovery_applies_open_webui_user_access_filter(monkeypatch):
    calls = []
    raw = [{'id': 'admin-only'}, {'id': 'shared'}]

    async def get_all_models(request, refresh, user):
        calls.append(('all', refresh, user.id))
        return raw

    async def get_filtered_models(models, user):
        calls.append(('filtered', list(models), user.id))
        return [models[1]]

    models_module = types.ModuleType('open_webui.utils.models')
    models_module.get_all_models = get_all_models
    models_module.get_filtered_models = get_filtered_models
    utils_module = types.ModuleType('open_webui.utils')
    utils_module.__path__ = []
    utils_module.models = models_module
    open_webui_module = types.ModuleType('open_webui')
    open_webui_module.__path__ = []
    open_webui_module.utils = utils_module
    monkeypatch.setitem(sys.modules, 'open_webui', open_webui_module)
    monkeypatch.setitem(sys.modules, 'open_webui.utils', utils_module)
    monkeypatch.setitem(sys.modules, 'open_webui.utils.models', models_module)

    result = asyncio.run(runner._fetch_models(object(), types.SimpleNamespace(id='member')))

    assert result == [{'id': 'shared'}]
    assert calls == [('all', False, 'member'), ('filtered', raw, 'member')]


@pytest.mark.parametrize('mode', ['independent', 'shared'])
def test_c21_captured_interface_payload_admits_persists_and_routes_exact_smoke(client, library, monkeypatch, captured, mode):
    sample = Path(__file__).parent / 'fixtures/Projects-Smoke'
    output = __import__('io').BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
        for path in sorted(sample.rglob('*')):
            if path.is_file():
                package.write(path, path.relative_to(sample).as_posix())
    archive = output.getvalue()
    zip_before = hashlib.sha256(archive).hexdigest()
    response = client.post('/api/magazine/library/import', headers=A, json={
        'file_name':'Smoke.zip', 'project_id':'Smoke',
        'file_data_b64':base64.b64encode(archive).decode()})
    assert response.status_code == 201, response.text
    preview = client.get('/api/magazine/library/Smoke', headers=A).json()
    original = copy.deepcopy(preview['document'])
    assert original == captured['validated_document']
    source_before = inventory(library)
    async def models(app, user):
        return [{'id':'mock-first'}, {'id':'mock-second'}] if user.id == 'test-a' else []
    monkeypatch.setattr(runner, '_fetch_models', models)
    body = copy.deepcopy(captured['requests'][mode])
    response = client.post('/api/magazine/runs', headers=A, json=body)
    assert response.status_code == 200, response.text
    ready = response.json(); rid = ready['run_id']; owner = 'test-a'
    assert ready['status'] == 'READY' and ready['model_map'] == body['model_map']
    assert runner._status_payload(owner,rid)['execution'] is None
    document_path = Path(runner._mag_path(owner,rid))
    persisted = json.loads(document_path.read_text())
    assert persisted == json.loads(body['magazine'])
    assert runner._load_meta(owner,rid)['model_map'] == body['model_map']
    assert persisted['schema_version'] == original['schema_version']
    assert [(c['id'],c['prompt']) for c in persisted['cartridges']] == [(c['id'],c['prompt']) for c in original['cartridges']]
    assert [c['job_type'] for c in persisted['cartridges']] == (['first','second'] if mode == 'independent' else ['acceptance','acceptance'])
    calls=[]
    async def dispatch(prompt,model_id):
        calls.append((prompt,model_id))
        return {'Reply with exactly ZIP_PROJECT_FIRST\n':'ZIP_PROJECT_FIRST',
                'Reply with exactly ZIP_PROJECT_SECOND\n':'ZIP_PROJECT_SECOND'}[prompt]
    core = runner._mag(); assert core['ok']
    prepared = core['rp'].prepare_run(str(document_path),runner._run_base(owner,rid),owner)
    assert prepared.state_path == runner._state_path(owner,rid)
    assert prepared.lock_path == runner._lock_path(owner,rid)
    final = asyncio.run(core['ar'].run_dispatch(str(document_path),prepared.state_path,prepared.lock_path,
                                               owner,prepared,runner._load_meta(owner,rid)['model_map'],dispatch))
    assert final['status'] == 'COMPLETED' and final['done'] == ['first','second']
    assert calls == [(original['cartridges'][0]['prompt'],'mock-first'),
                     (original['cartridges'][1]['prompt'],'mock-second' if mode == 'independent' else 'mock-first')]
    results = client.get('/api/magazine/runs/'+rid+'/results',headers=A).json()
    assert results['results'] == {'first':'ZIP_PROJECT_FIRST','second':'ZIP_PROJECT_SECOND'}
    # A subsequent preparation must not rewrite the completed historical run.
    history_before = inventory(Path(runner._run_base(owner,rid)))
    other = captured['requests']['shared' if mode == 'independent' else 'independent']
    assert client.post('/api/magazine/runs',headers=A,json=other).status_code == 200
    assert inventory(Path(runner._run_base(owner,rid))) == history_before
    assert client.post('/api/magazine/runs/'+rid+'/recover',headers=A).status_code == 409
    assert client.post('/api/magazine/runs/'+rid+'/start',headers=A).status_code == 409
    assert inventory(Path(runner._run_base(owner,rid))) == history_before
    assert client.get('/api/magazine/runs/'+rid,headers=B).status_code == 404
    assert client.get('/api/magazine/runs/'+rid+'/results',headers=B).status_code == 404
    assert client.get('/api/magazine/runs',headers=B).json()['runs'] == []
    assert inventory(library) == source_before
    assert client.get('/api/magazine/library/Smoke',headers=A).json()['document'] == original
    assert hashlib.sha256(archive).hexdigest() == zip_before
    print(json.dumps({'mode':mode,'persisted_map':ready['model_map'],
                      'dispatch_models':[model for _,model in calls], 'results':results['results'],
                      'canonical_source_and_history_unchanged':True}))


@pytest.mark.parametrize('fault', ['missing_key','unavailable_model','invalid_schema'])
def test_c21_existing_server_admission_remains_authoritative(client, captured, fault):
    body = copy.deepcopy(captured['requests']['independent'])
    body['model_map'] = {'first':'ornith:9b-q8_0','second':'ornith:9b-q8_0'}
    if fault == 'missing_key': del body['model_map']['second']
    elif fault == 'unavailable_model': body['model_map']['second'] = 'not-admitted'
    else:
        doc=json.loads(body['magazine']);doc['cartridges'][1]['unexpected']='not allowed';body['magazine']=json.dumps(doc)
    # Existing endpoint wraps strict-loader errors as 500; this task does not
    # change that contract. Either way invalid schema must never admit a run.
    if fault == 'invalid_schema':
        with pytest.raises(Exception, match='Unknown cartridge fields'):
            runner._load_package_from_upload('', '', 'json', body['magazine'])
    assert client.post('/api/magazine/runs',headers=A,json=body).status_code == (500 if fault == 'invalid_schema' else 400)
    assert client.get('/api/magazine/runs',headers=A).json()['runs'] == []
