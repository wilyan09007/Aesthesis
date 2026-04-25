"""Top-level pytest conftest.

Adds both `tribe_service/` and `backend/` to sys.path so tests can
`import tribe_neural...` and `import aesthesis...` without an editable
install. Keeps the dev loop fast.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent
for sub in ("tribe_service", "backend"):
    p = str((_ROOT / sub).resolve())
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("LOG_LEVEL", "WARNING")  # noisy at INFO across many tests
