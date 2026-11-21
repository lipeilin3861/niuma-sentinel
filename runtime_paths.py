"""Keep packaged resources and writable user data in separate directories."""

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
FROZEN = getattr(sys, "frozen", False)
USER_DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "PlayAtelier" / "NiuMaSentinel"
SETTINGS_ROOT = USER_DATA if FROZEN else ROOT
