"""Agent-facing automation pipelines (local blocks, watch orchestration)."""

from benchgate.pipeline.local_blocks import load_blocks_config, sync_local_blocks

__all__ = ["load_blocks_config", "sync_local_blocks"]
