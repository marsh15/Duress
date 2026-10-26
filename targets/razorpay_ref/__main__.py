"""``python -m razorpay_ref init-db --db PATH --seed SEED.json --variant fixed``."""

from __future__ import annotations

import argparse
import sys

from .common.db import init_db
from .common.schema import VARIANTS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m razorpay_ref")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db", help="create the schema and apply the seed, then exit")
    init.add_argument("--db", required=True, help="path to the SQLite database")
    init.add_argument("--seed", required=True, help="path to the seed JSON document")
    init.add_argument("--variant", required=True, choices=VARIANTS)

    args = parser.parse_args(argv)
    if args.command == "init-db":
        init_db(args.db, args.seed, args.variant)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
