"""Popper CLI.

    python -m popper.cli seed     # populate alpha memory with example hypotheses
    python -m popper.cli stats    # print current counts
    python -m popper.cli serve    # run the API for the dashboard
"""

import sys

from popper.config import settings


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "seed":
        from popper.seed import seed
        seed()
    elif cmd == "stats":
        from popper.memory import get_memory
        import json
        print(json.dumps(get_memory().stats(), indent=2))
    elif cmd == "serve":
        import uvicorn
        uvicorn.run("popper.api.main:app", host=settings.api_host, port=settings.api_port, reload=True)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
