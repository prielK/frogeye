"""`python -m app.snapshot`: restore the demo snapshot into an empty database (entrypoint).

Reads DATABASE_URL and DEMO_SNAPSHOT (auto | off, default auto) from the environment.
Always exits 0: a database it does not restore into starts the normal way.
"""

import os

from app.snapshot.restore import restore


def main() -> None:
    restore(os.environ["DATABASE_URL"], mode=os.environ.get("DEMO_SNAPSHOT", "auto"))


if __name__ == "__main__":
    main()
