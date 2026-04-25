"""TRIBE v2 service — wraps tribev2.demo_utils.TribeModel as an HTTP API.

Public entry points:
- `tribe_neural.api`       — FastAPI app
- `tribe_neural.worker`    — ARQ worker settings
- `tribe_neural.pipeline`  — process_video_timeline (the function the worker dispatches)

See DESIGN.md §5 for the full architecture spec.
"""

__version__ = "0.1.0.0"
