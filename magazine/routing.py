"""Deterministic per-cartridge model routing for Magazine."""


def resolve_model(model_key: str, model_map: dict) -> str:
    """Return the concrete model ID mapped to *model_key* in *model_map*."""
    if not isinstance(model_key, str) or not model_key.strip():
        raise TypeError("model_key must be a non-empty string")
    if not isinstance(model_map, dict):
        raise TypeError("model_map must be a dict")
    if model_key not in model_map:
        raise KeyError(f"Unknown model key: {model_key!r}")
    model_id = model_map[model_key]
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("mapped model ID must be a non-empty string")
    return model_id
