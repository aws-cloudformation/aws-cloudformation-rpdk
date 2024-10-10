"""This sub command generates code from the project and resource schema.

Projects can be created via the 'init' sub command.
"""
import logging

from .project import Project

LOG = logging.getLogger(__name__)


def generate(args):
    project = Project()
    project.load(args)
    project.generate(
        args.endpoint_url,
        args.region,
        args.local_only,
        args.target_schemas,
        args.profile,
    )
    project.generate_docs()
    project.generate_canary_files(
        args.local_code_generation,
    )
    LOG.warning("Generated files for %s", project.type_name)


def setup_subparser(subparsers, parents):
    # see docstring of this file
    parser = subparsers.add_parser("generate", description=__doc__, parents=parents)
    parser.set_defaults(command=generate)

    parser.add_argument("--endpoint-url", help="CloudFormation endpoint to use.")
    parser.add_argument("--region", help="AWS Region to submit the type.")
    parser.add_argument(
        "--local-only", action="store_true", help="Only load local target info"
    )
    parser.add_argument(
        "--target-schemas", help="Path to target schemas.", nargs="*", default=[]
    )
    parser.add_argument("--profile", help="AWS profile to use.")
    parser.add_argument(
        "--local-code-generation",
        action="store_true",
        help="Enable local code generation.",
    )
