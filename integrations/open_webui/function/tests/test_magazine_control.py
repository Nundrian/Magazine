"""C2 local page contract and unchanged-engine regression evidence."""
import base64
import io
import re
import zipfile
from pathlib import Path
import magazine_ef as runner
from test_magazine_library import client, library, A, B

def test_c2_page_exposes_existing_controls_in_plain_language():
    for label in ("Your workflows", "Set up", "Run workflow", "Results and previous work",
                  "Prepare workflow", "Start workflow", "Prepare to continue", "Check progress"):
        assert label in runner._HTML
    assert "Stop workflow" in runner._HTML
    for control in ("appearance", "btn-start", "btn-stop", "btn-recover", "history-runs", "saved-results"):
        assert f'id="{control}"' in runner._HTML


def test_stop_control_does_not_prepare_or_start_a_workflow():
    assert 'el("btn-stop").addEventListener("click", stopRun)' in runner._HTML
    stop_body = runner._HTML.split("async function stopRun(){", 1)[1].split("function checkRun", 1)[0]
    assert 'api(runPath(v) + "/stop", "POST")' in stop_body
    assert 'runAction("start")' not in stop_body
    assert '/api/chat/completions' not in stop_body


def test_page_has_unique_ids_and_no_external_frontend_dependencies():
    ids = re.findall(r'\bid="([^"]+)"', runner._HTML)
    assert len(ids) == len(set(ids))
    assert not re.search(r'<script\b[^>]*\bsrc=', runner._HTML)
    assert not re.search(r'<link\b[^>]*\bhref=', runner._HTML)
    assert 'data-theme="dark"' in runner._HTML


def test_embedded_core_matches_public_canonical_source_byte_for_byte():
    repository = Path(__file__).resolve().parents[4]
    expected = sorted((repository / "magazine").rglob("*.py"))
    archive_bytes = base64.b64decode(runner._MAG_ZIP_B64.strip())
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert sorted(archive.namelist()) == [
            path.relative_to(repository).as_posix() for path in expected
        ]
        for path in expected:
            name = path.relative_to(repository).as_posix()
            assert archive.read(name) == path.read_bytes(), name


def test_c2_page_still_requires_authentication_and_serves_same_source(client):
    assert client.get("/magazine").status_code == 401
    for user in (A, B):
        response = client.get("/magazine", headers=user)
        assert response.status_code == 200
        assert response.text == runner._HTML
        assert response.headers["content-type"].startswith("text/html")
