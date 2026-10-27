"""forge-runner — turns a Sheshnag finetune job into a trained LoRA adapter.

Generates the engine's config, runs LLaMA-Factory as a supervised subprocess,
streams its progress back, and uploads the adapter. Separate from the batch
daemon on purpose: a training run is not a batch of prompts.
"""
