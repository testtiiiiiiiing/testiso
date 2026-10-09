"""PyInstaller's file entry point; source runs with python -m winslim."""

import sys
from pathlib import Path

# PyInstaller analyzes this file with tools as its initial search directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from winslim.__main__ import main

if __name__ == "__main__":
    main()
