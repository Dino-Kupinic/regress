"""Regress: improve AI-generated tests using mutation testing as feedback."""

__version__ = "0.1.0"


def main() -> None:
    from regress.cli import main as cli_main

    cli_main()
