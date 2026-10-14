"""The public import artifact matches Open WebUI 0.11.3's native importer."""
import json
from pathlib import Path


def test_native_json_import_contains_only_the_verified_function_and_public_metadata():
    root=Path(__file__).resolve().parents[1]
    exported=json.loads((root / "magazine-function.json").read_text(encoding="utf-8"))
    assert len(exported)==1
    record=exported[0]
    assert set(record)=={"id","name","content","meta"}
    assert record["id"]=="magazine" and record["name"]=="Magazine"
    assert record["content"]==(root / "magazine_ef.py").read_text(encoding="utf-8")
    assert set(record["meta"])=={"description"}
    assert "0.1.0-beta.5" in record["meta"]["description"]
