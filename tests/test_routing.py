import pytest

from magazine.routing import resolve_model

MODEL_MAP = {"research": "model-a", "synthesis": "model-b"}


def test_resolve_known_model_key():
    assert resolve_model("research", MODEL_MAP) == "model-a"


def test_unknown_model_key_rejected():
    with pytest.raises(KeyError, match="Unknown model key"):
        resolve_model("missing", MODEL_MAP)


@pytest.mark.parametrize("value", ["", "   ", None, 1])
def test_invalid_model_key_rejected(value):
    with pytest.raises(TypeError):
        resolve_model(value, MODEL_MAP)


def test_non_dict_map_rejected():
    with pytest.raises(TypeError):
        resolve_model("research", [])


@pytest.mark.parametrize("mapped", ["", "   ", None])
def test_invalid_mapped_model_rejected(mapped):
    with pytest.raises(ValueError):
        resolve_model("research", {"research": mapped})
