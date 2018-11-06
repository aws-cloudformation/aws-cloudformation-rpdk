"""This sub command tests basic functionality of the
resource handler given a test resource and an endpoint.
"""
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest

from .argutils import TextFileType
from .contract.contract_plugin import ContractPlugin
from .contract.transports import LocalLambdaTransport
from .data_loaders import copy_resource

LOG = logging.getLogger(__name__)


@contextmanager
def temporary_ini_file():
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="pytest_", suffix=".ini"
    ) as temp:
        LOG.debug("temporary pytest.ini path: %s", temp.name)
        path = Path(temp.name).resolve(strict=True)
        copy_resource(__name__, "data/pytest-contract.ini", path)
        yield str(path)


def local_lambda(args):
    transport = LocalLambdaTransport(args.endpoint, args.function_name)
    resource_def_file = json.load(args.resource_def_file)
    resource_file = json.load(args.resource_file)
    updated_resource_file = json.load(args.updated_resource_file)

    with temporary_ini_file() as path:
        pytest_args = ["--pyargs", "rpdk.contract.suite", "-c", path]
        if args.test_types:
            pytest_args.extend(["-k", args.test_types])
        pytest.main(
            pytest_args,
            plugins=[
                ContractPlugin(
                    transport, resource_file, updated_resource_file, resource_def_file
                )
            ],
        )


def setup_subparser(subparsers, parents):
    # see docstring of this file
    parser = subparsers.add_parser("test", description=__doc__, parents=parents)
    # need to set this, so the help of this specific subparser is printed,
    # not the parent's help
    parser.set_defaults(command=lambda args: parser.print_help())

    test_subparsers = parser.add_subparsers(help="Type of transport to use for testing")
    local_lambda_subparser = test_subparsers.add_parser("local-lambda")
    local_lambda_subparser.set_defaults(command=local_lambda)
    local_lambda_subparser.add_argument(
        "resource_file", help="Example resource model", type=TextFileType("r")
    )
    local_lambda_subparser.add_argument(
        "updated_resource_file",
        help="Additional resource model to be used in update specific tests",
        type=TextFileType("r"),
    )
    local_lambda_subparser.add_argument(
        "resource_def_file",
        help="The definition of the resource that the handler provisions",
        type=TextFileType("r"),
    )
    local_lambda_subparser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:3001",
        help="The endpoint at which the handler can be invoked",
    )
    local_lambda_subparser.add_argument(
        "--function-name",
        default="Handler",
        help="The logical lambda function name in the SAM template",
    )
    local_lambda_subparser.add_argument(
        "--test-types", default=None, help="The type of contract tests to be run."
    )
