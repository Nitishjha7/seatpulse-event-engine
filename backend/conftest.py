"""
Add the backend root to sys.path for pytest.

tests/ has no __init__.py, so pytest only puts tests/ itself on sys.path
by default — `import core.database` would fail otherwise. This file sits
in the backend root and pytest loads it first, so the fix goes here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
