from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "src" / "gbrain_ops", ROOT / "adapters" / "telegram"):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)
