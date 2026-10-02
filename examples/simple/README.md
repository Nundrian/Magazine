# Simple two-cartridge workflow

Run this unpacked folder using `run_magazine` with an injected fresh-context
dispatch, or the `run_magazine_open_webui` wrapper shown in the project README.

The first cartridge requests a fixed token. The second asks an independent
arithmetic question. They execute in manifest list order and do not share
conversation messages or feed one result into the next prompt.

`concise` and `reasoning` are arbitrary routing keys, not installed model names
or reasoning settings. Map them to concrete harness model IDs:

```python
model_map = {
    "concise": "YOUR_FIRST_MODEL_ID",
    "reasoning": "YOUR_SECOND_MODEL_ID",
}
```

Choose different IDs to exercise per-cartridge model routing. Both keys can
also resolve to the same model. Magazine saves the exact returned result before
advancing; it does not check whether the fixed token or arithmetic answer is
correct. Use a fresh state directory for a new run.
