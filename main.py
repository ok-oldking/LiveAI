from pathlib import Path
import sys


# Shortcuts launch this script directly, without the launcher's PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from live_gpt.app import main


if __name__ == "__main__":
    raise SystemExit(main())
