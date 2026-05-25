"""Token universe — loaded from symbols.json which ships with the code.
To update the universe, regenerate symbols.json from the divergence scanner."""
import json
from pathlib import Path

_HERE = Path(__file__).parent

def load_symbols() -> list[str]:
    return json.loads((_HERE / "symbols.json").read_text())
