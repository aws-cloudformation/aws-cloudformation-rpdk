"""This sub command uploads the resource type to CloudFormation.

Projects can be created via the 'init' sub command.
"""
import logging

from .project import Project

LOG = logging.getLogger(__name__)


def package(_args):
    project = Project()
    project.load()
    project.submit(
        dry_run=True,
        endpoint_url=False,
        region_name=False,
        role_arn=False,
        use_role=False,
        set_default=False,
    )


def setup_subparser(subparsers, parents):
    parser = subparsers.add_parser("package", description=__doc__, parents=parents)
    parser.set_defaults(command=package)
