"""Trading bot entry point. Defaults to paper trading; use --check for offline validation."""
import argparse
import asyncio
import logging
import sys


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate imports and configuration without network or orders")
    parser.add_argument("--once", action="store_true", help="Run one cycle in the configured mode (may trade)")
    parser.add_argument("--paper", action="store_true", help="Force paper mode regardless of .env")
    args = parser.parse_args()
    import config
    if args.paper:
        config.DRY_RUN = True
    from core.runtime import run
    from core.indicators import compute_indicators
    from core.settings import validate_settings, process_lock
    validate_settings(config)
    if args.check:
        print("Startup check passed. No network calls or orders placed.")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    with process_lock():
        try:
            asyncio.run(run(once=args.once))
        except KeyboardInterrupt:
            print("Stopped. Exchange protective orders remain active.")


if __name__ == "__main__":
    main()
