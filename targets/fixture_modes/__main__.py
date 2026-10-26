"""``python -m fixture_modes init-db --db PATH --seed SEED.json --mode MODE``."""

from __future__ import annotations

import argparse

from razorpay_ref.common.db import init_db

from . import MODES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fixture_modes")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db")
    init.add_argument("--db", required=True)
    init.add_argument("--seed", required=True)
    init.add_argument("--mode", required=True, choices=MODES)
    args = parser.parse_args(argv)
    if args.command == "init-db":
        # The fixture schema is the vulnerable schema: no UNIQUE on fulfilments,
        # so dup_then_hang can genuinely double-fulfil.
        init_db(args.db, args.seed, "vulnerable")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())