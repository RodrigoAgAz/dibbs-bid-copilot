import sys
from pathlib import Path

ML_ROOT = Path(__file__).resolve().parents[1]
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))

import shutil  # noqa: E402
import tempfile  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture()
def tmp_dir():
    """Like tmp_path, but via tempfile (pytest's shared basetemp can be unwritable on locked-down Windows)."""
    d = Path(tempfile.mkdtemp(prefix="dibbs-ml-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)
