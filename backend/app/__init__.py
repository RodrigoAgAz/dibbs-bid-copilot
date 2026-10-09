"""DIBBS Bid Copilot backend."""

import sys
from pathlib import Path

# The ML package lives in ../ml (PYTHONPATH covers it in Docker); make it importable everywhere.
_ML = Path(__file__).resolve().parents[2] / "ml"
if _ML.is_dir() and str(_ML) not in sys.path:
    sys.path.append(str(_ML))
