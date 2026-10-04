"""Regress: improve AI-generated tests using mutation testing as feedback."""

__version__ = "0.5.0"


def main() -> None:
    from regress.cli import main as cli_main

    cli_main()
