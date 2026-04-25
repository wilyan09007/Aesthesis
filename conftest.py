"""Top-level pytest conftest.

Adds both `tribe_service/` and `aesthesis_app/` to sys.path so tests can
`import tribe_neural...` and `import aesthesis...` without an editable
install. Keeps the dev loop fast.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parent
for sub in ("tribe_service", "aesthesis_app"):
    p = str((_ROOT / sub).resolve())
    if p not in sys.path:
        sys.path.insert(0, p)

# Force mock mode for everything during tests. Real TRIBE / Gemini are
# never invoked from CI.
os.environ.setdefault("TRIBE_MOCK_MODE", "1")
os.environ.setdefault("GEMINI_MOCK_MODE", "1")
os.environ.setdefault("LOG_LEVEL", "WARNING")  # noisy at INFO across many tests
