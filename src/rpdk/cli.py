"""This tool provides support for creating CloudFormation resource providers.
"""
import argparse
import logging
import sys
import time
from logging.config import dictConfig

from .data_loaders import resource_yaml
from .generate import setup_subparser as generate_setup_subparser
from .init import setup_subparser as init_setup_subparser
from .project_settings import setup_subparser as project_settings_setup_subparser
from .test import setup_subparser as test_setup_subparser
from .validate import setup_subparser as validate_setup_subparser

EXIT_UNHANDLED_EXCEPTION = 127


class UTCFormatter(logging.Formatter):
    converter = time.gmtime


def setup_logging(verbosity):
    """Configure logging with a variable verbosity level (0, 1, 2)."""
    if verbosity > 1:
        level = logging.DEBUG
    elif verbosity > 0:
        level = logging.INFO
    else:
        level = logging.WARNING

    logging_config = resource_yaml(__name__, "data/logging.yaml")
    logging_config["handlers"]["console"]["level"] = level
    dictConfig(logging_config)


def unittest_patch_setup_subparser(_subparsers, _parents):
    pass


def main(args_in=None):
    """The entry point for the CLI."""
    log = None
    try:
        # see docstring of this file
        parser = argparse.ArgumentParser(description=__doc__)
        # the default command just prints the help message
        # subparsers should set their own default commands
        # also need to set verbose here because now it only gets set if a
        # subcommand is run (which is okay, the help doesn't need it)
        parser.set_defaults(command=lambda args: parser.print_help(), verbose=0)

        base_subparser = argparse.ArgumentParser(add_help=False)
        # shared arguments
        base_subparser.add_argument(
            "-v",
            "--verbose",
            action="count",
            default=0,
            help="Increase the output verbosity. Can be specified multiple times.",
        )
        parents = [base_subparser]

        subparsers = parser.add_subparsers(dest="subparser_name")
        init_setup_subparser(subparsers, parents)
        validate_setup_subparser(subparsers, parents)
        generate_setup_subparser(subparsers, parents)
        project_settings_setup_subparser(subparsers, parents)
        test_setup_subparser(subparsers, parents)
        unittest_patch_setup_subparser(subparsers, parents)
        args = parser.parse_args(args=args_in)

        setup_logging(args.verbose)

        log = logging.getLogger(__name__)
        log.debug("Logging set up successfully")

        args.command(args)
    except Exception:  # pylint: disable=broad-except
        print("=== Unhandled exception ===", file=sys.stderr)
        print("Please report this issue to the team.", file=sys.stderr)
        print(
            "Issue tracker: "
            "https://github.com/awslabs/aws-cloudformation-rpdk/issues",
            file=sys.stderr,
        )

        if log:
            print("Please include the log file 'rpdk.log'", file=sys.stderr)
            log.debug("Unhandled exception", exc_info=True)
        else:
            print("Please include this information:", file=sys.stderr)
            import traceback

            traceback.print_exc()
        raise SystemExit(EXIT_UNHANDLED_EXCEPTION)
