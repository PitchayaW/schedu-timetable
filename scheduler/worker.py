from __future__ import annotations

import pickle
import sys
from pathlib import Path

from .solver import solve_schedule


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m scheduler.worker INPUT.pkl OUTPUT.pkl")
    input_path, output_path = map(Path, sys.argv[1:])
    bundle, parameters = pickle.loads(input_path.read_bytes())
    result = solve_schedule(bundle, parameters)
    output_path.write_bytes(pickle.dumps(result))


if __name__ == "__main__":
    main()
