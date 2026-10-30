"""showntell: narrated slide walkthroughs for coding agents, voiced on your machine."""

__version__ = "0.2.1"


def main() -> None:
    from .cli import main as cli_main

    cli_main()
