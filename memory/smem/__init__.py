# SPDX-License-Identifier: MIT
"""smem -- a local memory layer served by an Intel VCA 2 card.

A Supermemory-shaped API (container tags, extracted memories, temporal truth,
soft-delete forgetting, recency decay) running entirely on three Xeon E3-1585L
v5 nodes on a discontinued 2016 PCIe accelerator. No API keys, no egress.
"""

from .engine import Engine
from .models import Models
from .pool import Pool
from .store import Store

__all__ = ["Engine", "Models", "Pool", "Store"]
__version__ = "0.1.0"
