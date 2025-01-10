"""This sub command adds credentials to a payload, and sends it to a Lambda
function. The function is re-invoked while the IN_PROGRESS status is returned.

Projects can be created via the 'init' sub command.
"""
# pylint: disable=protected-access
import json
import logging
from argparse import FileType
from time import sleep

from .contract.hook_client import HookClient
from .contract.interface import Action, HookInvocationPoint, HookStatus, OperationStatus
from .contract.resource_client import ResourceClient
from .exceptions import SysExitRecommendedError
from .project import ARTIFACT_TYPE_HOOK, ARTIFACT_TYPE_RESOURCE, Project
from .test import _sam_arguments, _validate_sam_args

LOG = logging.getLogger(__name__)


def get_payload_to_log(payload, artifact_type):
    if artifact_type == ARTIFACT_TYPE_HOOK:
        return {
            "hookTypeName": payload["hookTypeName"],
            "actionInvocationPoint": payload["actionInvocationPoint"],
            "requestData": {
                "targetName": payload["requestData"]["targetName"],
                "targetLogicalId": payload["requestData"]["targetLogicalId"],
                "targetModel": payload["requestData"]["targetModel"],
            },
            "awsAccountId": payload["awsAccountId"],
            "clientRequestToken": payload["clientRequestToken"],
        }

    return {
        "callbackContext": payload["callbackContext"],
        "action": payload["action"],
        "requestData": {
            "resourceProperties": payload["requestData"]["resourceProperties"],
            "previousResourceProperties": payload["requestData"][
                "previousResourceProperties"
            ],
        },
        "region": payload["region"],
        "awsAccountId": payload["awsAccountId"],
        "bearerToken": payload["bearerToken"],
    }


def get_contract_client(args, project):
    if project.artifact_type == ARTIFACT_TYPE_HOOK:
        return HookClient(
            args.function_name,
            args.endpoint,
            args.region,
            project.schema,
            {},
            executable_entrypoint=project.executable_entrypoint,
            docker_image=args.docker_image,
            profile=args.profile,
        )

    return ResourceClient(
        args.function_name,
        args.endpoint,
        args.region,
        project.schema,
        {},
        executable_entrypoint=project.executable_entrypoint,
        docker_image=args.docker_image,
        profile=args.profile,
    )


def prepare_payload_for_reinvocation(payload, response, artifact_type):
    if artifact_type == ARTIFACT_TYPE_RESOURCE:
        payload["callbackContext"] = response.get("callbackContext")
        if ("resourceModel" in response) and ("requestData" in payload):
            payload["requestData"]["resourceProperties"] = response.get("resourceModel")

    return payload


def invoke(args):
    _validate_sam_args(args)
    project = Project()
    project.load()

    client = get_contract_client(args, project)

    try:
        request = json.load(args.request)
    except ValueError as e:
        raise SysExitRecommendedError(f"Invalid JSON: {e}") from e

    if project.artifact_type == ARTIFACT_TYPE_HOOK:
        status_type = HookStatus
        in_progress_status = HookStatus.IN_PROGRESS
        action_invocation_point = HookInvocationPoint[args.action_invocation_point]
        payload = client._make_payload(
            action_invocation_point,
            request["targetName"],
            request["targetModel"],
        )
    else:
        status_type = OperationStatus
        in_progress_status = OperationStatus.IN_PROGRESS
        action = Action[args.action]
        payload = client._make_payload(
            action,
            request["desiredResourceState"],
            request["previousResourceState"],
            request.get("typeConfiguration"),
            stackId=request.get("stackId")
        )  # pylint: disable=too-many-function-args

    current_invocation = 0

    try:
        while _needs_reinvocation(args.max_reinvoke, current_invocation):
            print("=== Handler input ===")

            payload_to_log = get_payload_to_log(payload, project.artifact_type)
            print(json.dumps({**payload_to_log}, indent=2))

            response = client._call(payload)
            current_invocation = current_invocation + 1
            print("=== Handler response ===")
            print(json.dumps(response, indent=2))
            status = status_type[response["status"]]

            if status != in_progress_status:
                break

            sleep(response.get("callbackDelaySeconds", 0))
            prepare_payload_for_reinvocation(payload, response, project.artifact_type)
    except KeyboardInterrupt:
        pass


def _needs_reinvocation(max_reinvoke, current_invocation):
    return max_reinvoke is None or max_reinvoke >= current_invocation


def _setup_invoke_subparser(subparser):
    subparser.add_argument(
        "request",
        type=FileType("r", encoding="utf-8"),
        help="A JSON file that contains the request to invoke the function with.",
    )
    subparser.add_argument(
        "--max-reinvoke",
        type=int,
        default=None,
        help=(
            "Maximum number of IN_PROGRESS re-invocations allowed before "
            "exiting. If not specified, will continue to "
            "re-invoke until terminal status is reached."
        ),
    )
    subparser.add_argument(
        "--docker-image",
        help=(
            "Docker image name to run. If specified, invoke will use docker instead "
            "of SAM"
        ),
    )


def setup_subparser(subparsers, parents):
    # see docstring of this file
    parser = subparsers.add_parser("invoke", description=__doc__, parents=parents)
    parser.set_defaults(command=invoke)

    invoke_subparsers = parser.add_subparsers(dest="subparser_name")
    invoke_subparsers.required = True
    resource_parser = invoke_subparsers.add_parser("resource", description=__doc__)
    resource_parser.add_argument(
        "action",
        choices=list(Action.__members__),
        help="The provisioning action, i.e. which resource handler to invoke.",
    )
    _setup_invoke_subparser(resource_parser)

    hook_parser = invoke_subparsers.add_parser("hook", description=__doc__)
    hook_parser.add_argument(
        "action_invocation_point",
        choices=list(HookInvocationPoint.__members__),
        help=(
            "The provisioning action invocation point, i.e. which hook handler to"
            " invoke."
        ),
    )
    _setup_invoke_subparser(hook_parser)

    _sam_arguments(parser)
