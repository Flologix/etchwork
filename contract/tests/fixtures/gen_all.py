"""Regenerates every task's mutation fixtures (`./etchwork contract fixtures`).

Each generator runs in its own process: a generator's uuid sequence depends
on the footprint cache it warms itself (fixture_lib.write_all), so one
generator must never inherit another's warm cache."""
import subprocess
import sys
from pathlib import Path

# fixture directory under contract/tests/fixtures/ -> generator module
GENERATORS = {
    "keyfob": "gen_keyfob",
    "usbc-ldo": "gen_usbc_ldo",
    "usb-serial": "gen_usb_serial",
    "relay-driver": "gen_relay_driver",
}


def main(root) -> None:
    root = Path(root)
    for directory, module in GENERATORS.items():
        subprocess.run([sys.executable, "-m", f"contract.tests.fixtures.{module}",
                        str(root / directory)], check=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
