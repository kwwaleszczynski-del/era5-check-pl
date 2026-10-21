"""Command line interface.

Usage:
    python -m era5_check fetch                # IMGW-PIB station files and ERA5 point series -> data/
    python -m era5_check validate             # scores, energy impact, MCP folds -> results/*.csv
    python -m era5_check report               # PNG charts (docs/img), dashboard (docs/index.html), summary.json
    python -m era5_check run                  # all three
    KELMARSH_DATA_DIR=../wind-power-curve-kelmarsh/data python -m era5_check validate   # with the hub-height check
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from era5_check import pipeline

log = logging.getLogger("era5_check")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="era5_check", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in [("fetch", "download and cache the open data"), ("validate", "compute the result tables"),
                        ("report", "charts, dashboard and summary"), ("run", "fetch + validate + report")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--data-dir", type=Path, default=Path("data"), help="cache for downloaded data")
        p.add_argument("--results-dir", type=Path, default=Path("results"))
        p.add_argument("--docs-dir", type=Path, default=Path("docs"))
        p.add_argument("--max-weight", type=float, default=2000.0,
                       help="refuse Open-Meteo requests above this weight per 24 h (default 2000)")
        p.add_argument("--refresh", action="store_true", help="request the Open-Meteo series again")
        p.add_argument("--no-kelmarsh", action="store_true", help="skip the ERA5 request for the hub-height check")
        p.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    t0 = time.time()
    if args.command in ("fetch", "run"):
        pipeline.fetch(args.data_dir, max_weight=args.max_weight, refresh=args.refresh,
                       with_kelmarsh=not args.no_kelmarsh)
    if args.command in ("validate", "run"):
        pipeline.validate(args.data_dir, args.results_dir)
    if args.command in ("report", "run"):
        summary = pipeline.report(args.data_dir, args.results_dir, args.docs_dir)
        for k, v in summary.items():
            log.info("%s: %s", k, v)
    log.info("done in %.0f s", time.time() - t0)
    return 0
