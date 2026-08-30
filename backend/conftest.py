"""
Pytest ko backend root sys.path me daal do.

Kyu zaroori: hamare tests `tests/` folder me hain aur usme `__init__.py`
nahi hai. Us case me pytest sirf `tests/` ko sys.path me daalta hai, `/app`
ko nahi — to `import database` fail ho jata hai.

Ye file backend root me hai, isliye pytest ise pehle uthata hai aur yahi
path fix ho jata hai. (`tests/` me `__init__.py` daalna dusra tareeka hai,
par usse test files package ban jaati hain aur naam clash ho sakte hain.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
