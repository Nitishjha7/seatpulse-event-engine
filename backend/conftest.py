"""
Add the backend root to sys.path for pytest.

Required because tests reside in a `tests/` folder without an `__init__.py`.
By default, pytest only adds `tests/` to sys.path, causing `import database`
to fail.

Since this file is in the backend root, pytest loads it first, ensuring the
correct path is set. (Adding `__init__.py` to `tests/` is an alternative,
but it turns test files into a package, risking name collisions.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
