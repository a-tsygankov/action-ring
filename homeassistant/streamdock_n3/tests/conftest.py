"""Put the app directory on sys.path so tests can import its modules directly."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
