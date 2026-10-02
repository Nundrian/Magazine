import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from magazine.package import CartridgeRecord
from magazine.runtime_dispatch import dispatch_cartridge, dispatch_routed
from magazine.schema import MagazineSchemaError

MODEL_MAP = {"research": "model-a", "synthesis": "model-b"}
PROMPT = "Do the bounded task."


@patch("magazine.runtime_dispatch.resolve_model", return_value="model-a")
def test_dispatch_routed_resolves_model_and_dispatches(mock_resolve):
    dispatch = AsyncMock(return_value="result")
    result = asyncio.run(dispatch_routed("research", MODEL_MAP, dispatch, PROMPT))
    assert result == "result"
    mock_resolve.assert_called_once_with("research", MODEL_MAP)
    dispatch.assert_awaited_once_with(PROMPT, "model-a")


def test_dispatch_routed_resolution_failure_skips_dispatch():
    dispatch = AsyncMock()
    with pytest.raises(KeyError):
        asyncio.run(dispatch_routed("missing", MODEL_MAP, dispatch, PROMPT))
    dispatch.assert_not_awaited()


def test_dispatch_cartridge_legacy_dict():
    dispatch = AsyncMock(return_value="ok")
    cart = {"id": "c1", "job_type": "research", "prompt": PROMPT}
    assert asyncio.run(dispatch_cartridge(cart, MODEL_MAP, dispatch)) == "ok"
    dispatch.assert_awaited_once_with(PROMPT, "model-a")


def test_dispatch_cartridge_record():
    dispatch = AsyncMock(return_value="ok")
    rec = CartridgeRecord("c1", "synthesis", "cartridges/c1.md", Path("/tmp/c1.md"), PROMPT)
    assert asyncio.run(dispatch_cartridge(rec, MODEL_MAP, dispatch)) == "ok"
    dispatch.assert_awaited_once_with(PROMPT, "model-b")


def test_invalid_dict_is_rejected_before_dispatch():
    dispatch = AsyncMock()
    with pytest.raises(MagazineSchemaError):
        asyncio.run(dispatch_cartridge({"id": "c1", "job_type": "research"}, MODEL_MAP, dispatch))
    dispatch.assert_not_awaited()


def test_prompt_identity_is_preserved():
    prompt = "exact prompt\nwith whitespace\n"
    dispatch = AsyncMock(return_value="ok")
    cart = {"id": "c1", "job_type": "research", "prompt": prompt}
    asyncio.run(dispatch_cartridge(cart, MODEL_MAP, dispatch))
    assert dispatch.await_args.args[0] is prompt


def test_different_cartridges_route_to_different_models():
    dispatch = AsyncMock(side_effect=["a", "b"])
    asyncio.run(dispatch_cartridge({"id": "a", "job_type": "research", "prompt": "A"}, MODEL_MAP, dispatch))
    asyncio.run(dispatch_cartridge({"id": "b", "job_type": "synthesis", "prompt": "B"}, MODEL_MAP, dispatch))
    assert dispatch.await_args_list[0].args == ("A", "model-a")
    assert dispatch.await_args_list[1].args == ("B", "model-b")
