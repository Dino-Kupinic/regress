"""Regress: improve AI-generated tests using mutation testing as feedback."""

__version__ = "0.7.1"


def main() -> None:
    from regress.cli import main as cli_main

    cli_main()
