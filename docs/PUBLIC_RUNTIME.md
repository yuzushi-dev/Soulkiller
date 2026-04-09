# Public Runtime Contract

The OSS repo uses public, demo-safe runtime artifact names instead of the original private deployment names.

## Demo Artifact Names

- `event_log.sample.jsonl`
- `delivery_log.sample.jsonl`
- `model_profile.md`
- `model_portrait.md`
- `summary.json`

## Why These Names Exist

- They avoid leaking the original private deployment layout.
- They make the demo flow easier to understand from a clean checkout.
- They separate public examples from any production naming that may still exist in internal docs or adapters.

## Public Workflow

1. Configure the demo environment from `demo/config.example.env`.
2. Run `python -m soulkiller.demo_runner --output-dir demo/generated`.
3. Inspect `model_profile.md`, `model_portrait.md`, and `summary.json`.
4. Use the generated files for screenshots, examples, and reproducible verification.
