"""Start Paint Accountant: `python run.py` (see `python run.py --help`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paint_accountant.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
