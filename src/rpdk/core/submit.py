"""This sub command uploads the resource type to CloudFormation.

Projects can be created via the 'init' sub command.
"""
import logging

from .project import Project

LOG = logging.getLogger(__name__)


def submit(args):
    project = Project()
    project.load(args)
    # Use CLI override opposed to config file if use-docker or no-docker switch used
    if args.use_docker or args.no_docker:
        project.settings["use_docker"] = args.use_docker
        project.settings["no_docker"] = args.no_docker
    project.submit(
        args.dry_run,
        args.endpoint_url,
        args.region,
        args.role_arn,
        args.use_role,
        args.set_default,
        args.profile,
    )


def setup_subparser(subparsers, parents):
    # see docstring of this file
    parser = subparsers.add_parser("submit", description=__doc__, parents=parents)
    parser.set_defaults(command=submit)

    parser.add_argument(
        "--dry-run", action="store_true", help="Package the project, but do not submit."
    )
    parser.add_argument("--endpoint-url", help="CloudFormation endpoint to use.")
    parser.add_argument("--region", help="AWS Region to submit the resource type.")
    parser.add_argument(
        "--set-default",
        action="store_true",
        help="If registration is successful, set submitted version to the default.",
    )
    parser.add_argument("--profile", help="AWS profile to use.")
    role_group = parser.add_mutually_exclusive_group()
    role_group.add_argument(
        "--role-arn",
        help="Role ARN that CloudFormation will use when invoking handlers.",
    )
    role_group.add_argument(
        "--no-role",
        action="store_false",
        dest="use_role",
        help=(
            "Register the type without an explicit execution role "
            "(Will not be able to invoke AWS APIs)."
        ),
    )

    nodocker_group = parser.add_mutually_exclusive_group()
    nodocker_group.add_argument(
        "--use-docker",
        action="store_true",
        help="""Use docker for platform-independent packaging.
            This is highly recommended unless you are experienced
            with cross-platform packaging.""",
    )
    nodocker_group.add_argument(
        "--no-docker",
        action="store_true",
        help="""Generally not recommended unless you are experienced
            with cross-platform packaging.""",
    )
