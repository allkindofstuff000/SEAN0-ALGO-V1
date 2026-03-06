from __future__ import annotations

import os

from bot.main import main as modular_main


def main() -> None:
    """
    Default entrypoint for the modular trading engine.
    Set LEGACY_RUNTIME=1 to run the previous runtime.
    """
    if os.getenv("LEGACY_RUNTIME", "0").strip() == "1":
        from main_legacy import main as legacy_main

        legacy_main()
        return
    modular_main()


if __name__ == "__main__":
    main()
