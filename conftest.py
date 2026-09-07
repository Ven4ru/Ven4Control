import sys
from pathlib import Path


# Позволяет запускать pytest из корня репозитория без установки пакета.
SOURCE_ROOT = Path(__file__).resolve().parent / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
