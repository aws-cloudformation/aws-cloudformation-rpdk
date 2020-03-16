"""This sub command tests basic functionality of the resource in the project.

Projects can be created via the 'init' sub command.
"""
import json
import logging
from argparse import SUPPRESS
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from jinja2 import Environment, Template, meta
from jsonschema import Draft6Validator
from jsonschema.exceptions import ValidationError

from rpdk.core.jsonutils.pointer import fragment_decode

from .boto_helpers import create_sdk_session
from .contract.contract_plugin import ContractPlugin
from .contract.interface import Action
from .contract.resource_client import ResourceClient
from .data_loaders import copy_resource
from .exceptions import SysExitRecommendedError
from .project import Project

LOG = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "http://127.0.0.1:3001"
DEFAULT_FUNCTION = "TestEntrypoint"
DEFAULT_REGION = "us-east-1"

OVERRIDES_VALIDATOR = Draft6Validator(
    {
        "properties": {"CREATE": {"type": "object"}, "UPDATE": {"type": "object"}},
        "anyOf": [{"required": ["CREATE"]}, {"required": ["UPDATE"]}],
        "additionalProperties": False,
    }
)


def empty_override():
    return {"CREATE": {}}


@contextmanager
def temporary_ini_file():
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="pytest_", suffix=".ini"
    ) as temp:
        LOG.debug("temporary pytest.ini path: %s", temp.name)
        path = Path(temp.name).resolve(strict=True)
        copy_resource(__name__, "data/pytest-contract.ini", path)
        yield str(path)


def get_cloudformation_exports(region_name, endpoint_url):
    session = create_sdk_session(region_name)
    cfn_client = session.client("cloudformation", endpoint_url=endpoint_url)
    next_token = ""
    exports = {}
    while next_token is not None:
        result = (
            cfn_client.list_exports(NextToken=next_token)
            if next_token != ""
            else cfn_client.list_exports()
        )
        exports = {
            **exports,
            **{export["Name"]: export["Value"] for export in result["Exports"]},
        }
        next_token = None if "NextToken" not in result else result["NextToken"]
    return exports


def render_jinja(overrides_raw, region_name, endpoint_url):
    env = Environment(autoescape=True)
    parsed_content = env.parse(json.dumps(overrides_raw))
    variables = meta.find_undeclared_variables(parsed_content)
    if variables:
        exports = get_cloudformation_exports(region_name, endpoint_url)
        invalid_exports = []
        var_map = {}
        for variable in variables:
            if variable in exports:
                var_map[variable] = exports[variable]
            else:
                invalid_exports.append(variable)
        if invalid_exports:
            invalid_exports_message = (
                "Override file invalid: %s are not valid cloudformation exports."
                + "No Overrides will be applied"
            )
            LOG.warning(invalid_exports_message, invalid_exports)
            return empty_override()
        overrides_template = Template(json.dumps(overrides_raw))
        to_return = json.loads(overrides_template.render(var_map))
    else:
        overrides_template = Template(json.dumps(overrides_raw))
        to_return = json.loads(overrides_template.render())
    return to_return


def get_overrides(root, region_name, endpoint_url):
    if not root:
        return empty_override()

    path = root / "overrides.json"
    try:
        with path.open("r", encoding="utf-8") as f:
            overrides_raw = render_jinja(json.load(f), region_name, endpoint_url)
    except FileNotFoundError:
        LOG.debug("Override file '%s' not found. No overrides will be applied", path)
        return empty_override()

    try:
        OVERRIDES_VALIDATOR.validate(overrides_raw)
    except ValidationError as e:
        LOG.warning("Override file invalid: %s\n" "No overrides will be applied", e)
        return empty_override()

    overrides = empty_override()
    for operation, items_raw in overrides_raw.items():
        items = {}
        for pointer, obj in items_raw.items():
            try:
                pointer = fragment_decode(pointer, prefix="")
            except ValueError:
                LOG.warning("%s pointer '%s' is invalid. Skipping", operation, pointer)
            else:
                items[pointer] = obj
        overrides[operation] = items

    return overrides


def get_marker_options(schema):
    lowercase_actions = {action.lower() for action in Action}
    excluded_actions = lowercase_actions - schema.get("handlers", {}).keys()
    marker_list = ["not " + action for action in excluded_actions]
    return " and ".join(marker_list)


def test(args):
    project = Project()
    project.load()

    overrides = get_overrides(project.root, args.region, args.endpoint_url)

    plugin = ContractPlugin(
        ResourceClient(
            args.function_name,
            args.endpoint,
            args.region,
            project.schema,
            overrides,
            args.role_arn,
        )
    )

    with temporary_ini_file() as path:
        pytest_args = ["-c", path, "-m", get_marker_options(project.schema)]
        if args.passed_to_pytest:
            LOG.debug("extra args: %s", args.passed_to_pytest)
            pytest_args.extend(args.passed_to_pytest)
        LOG.debug("pytest args: %s", pytest_args)
        ret = pytest.main(pytest_args, plugins=[plugin])
        if ret:
            raise SysExitRecommendedError("One or more contract tests failed")


def setup_subparser(subparsers, parents):
    # see docstring of this file
    parser = subparsers.add_parser("test", description=__doc__, parents=parents)
    parser.set_defaults(command=test)

    _sam_arguments(parser)
    # this parameter can be used to pass additional arguments to pytest after `--`
    # for example,

    parser.add_argument(
        "--role-arn", help="Role used when performing handler operations."
    )

    parser.add_argument("--endpoint-url", help="CloudFormation endpoint to use.")

    parser.add_argument("passed_to_pytest", nargs="*", help=SUPPRESS)


def _sam_arguments(parser):
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=(
            "The endpoint at which the type can be invoked "
            f"(Default: {DEFAULT_ENDPOINT})"
        ),
    )
    parser.add_argument(
        "--function-name",
        default=DEFAULT_FUNCTION,
        help=(
            "The logical lambda function name in the SAM template "
            f"(Default: {DEFAULT_FUNCTION})"
        ),
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=(
            "The region used for temporary credentials " f"(Default: {DEFAULT_REGION})"
        ),
    )
