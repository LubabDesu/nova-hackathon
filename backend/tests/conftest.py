"""Pytest configuration for backend tests.

Adds the backend/ directory to sys.path so imports like
`from services.nova_act.booking_agent import ...` resolve without
requiring the package to be installed.
"""

import sys
from pathlib import Path

# backend/ directory (parent of this file's parent)
_BACKEND_DIR = Path(__file__).parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
