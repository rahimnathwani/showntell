"""showntell: narrated slide walkthroughs for coding agents, voiced on your machine."""

__version__ = "0.1.9"


def main() -> None:
    from .cli import main as cli_main

    cli_main()
