"""Run with python -m literature_brief from the project directory."""

from __future__ import annotations

import argparse
from datetime import date
import logging
import os
from pathlib import Path

from .config import API_KEY_VARIABLES, load_config
from .demo import demo_report
from .http import HttpClient, HttpError
from .pipeline import build_report
from .render import render_brief


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a weekly bioengineering literature brief.")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--output", type=Path, default=Path("site"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/scores"))
    parser.add_argument("--end-date", type=date.fromisoformat, help="Exclusive UTC end date (YYYY-MM-DD).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="Offline preview with clearly fictional papers.")
    mode.add_argument("--fetch-only", action="store_true", help="Fetch real metadata without making LLM calls.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = load_config(args.config)
        if args.demo:
            report, exit_code = demo_report(config, args.end_date), 0
        else:
            variable = API_KEY_VARIABLES[config["llm"]["provider"]]
            if not args.fetch_only and not os.environ.get(variable, "").strip():
                parser.error(f"Set {variable} for live assessment, or use --demo / --fetch-only.")
            email = config["sources"].get("contact_email", "")
            client = HttpClient(user_agent="WeeklyLiteratureBrief/1.0" + (f" (mailto:{email})" if email else ""))
            report, exit_code = build_report(config, end=args.end_date, cache_dir=args.cache_dir,
                                              fetch_only=args.fetch_only, client=client)
        page = render_brief(report, args.output)
        (args.output / ".nojekyll").touch()
        logging.info("Wrote %s", page.resolve())
        if exit_code:
            logging.error("Collection or scoring failed entirely. Diagnostic output was written; deployment should stop.")
        return exit_code
    except (ValueError, OSError, HttpError) as exc:
        logging.error("Cannot build brief: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
