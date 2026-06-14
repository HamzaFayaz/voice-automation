"""CLI entrypoint for Voice Automation.

Usage:
    voice-automation run              Start the voice automation loop
    voice-automation check            Run environment / dependency checks
    voice-automation download-model   Download the speech-recognition model
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure the Windows console handles UTF-8 for emoji and special characters.
if sys.platform == "win32":
    os.system("")  # Enable ANSI escape codes on Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from voice_automation import __version__

# ── Banner ───────────────────────────────────────────────────────────────────

_BANNER = rf"""
 __     __    _              _         _
 \ \   / /__ (_) ___ ___    / \  _   _| |_ ___
  \ \ / / _ \| |/ __/ _ \  / _ \| | | | __/ _ \
   \ V / (_) | | (_|  __/ / ___ \ |_| | || (_) |
    \_/ \___/|_|\___\___/_/_/  _\_\__,_|\__\___/
                          |_|
  Voice Automation v{__version__}  –  Speak & Type Anywhere
"""


def _cmd_run(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Start the voice-automation orchestrator."""
    from voice_automation import orchestrator  # lazy import

    orchestrator.run()


def _cmd_check(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Run environment checks."""
    from voice_automation import check  # lazy import

    passed = check.run_checks()
    sys.exit(0 if passed else 1)


def _cmd_download(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Download the speech-recognition model."""
    from voice_automation import downloader  # lazy import

    downloader.download()


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="voice-automation",
        description="Voice Automation – speak and type anywhere on Windows.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── run ───────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser("run", help="Start voice automation")
    run_parser.set_defaults(func=_cmd_run)

    # ── check ─────────────────────────────────────────────────────────────
    check_parser = subparsers.add_parser("check", help="Run environment checks")
    check_parser.set_defaults(func=_cmd_check)

    # ── download-model ────────────────────────────────────────────────────
    dl_parser = subparsers.add_parser("download-model", help="Download the ASR model")
    dl_parser.set_defaults(func=_cmd_download)

    return parser


def main(argv: list[str] | None = None) -> None:
    """Main entry point invoked by the console script or ``python -m``."""
    print(_BANNER)

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
