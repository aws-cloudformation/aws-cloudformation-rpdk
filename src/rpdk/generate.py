"""This sub command generates a basic resource provider code skeleton from a
resource provider definition and a given language.

Language-specific project settings can optionally be provided to further
customize the code generation.
"""
import logging
import os
from pathlib import Path

from .argutils import TextFileType
from .data_loaders import load_project_settings, load_resource_spec
from .plugin_registry import PLUGIN_REGISTRY, add_language_argument

LOG = logging.getLogger(__name__)


def generate(args):
    plugin = PLUGIN_REGISTRY[args.language]

    LOG.info("Loading the project settings...")
    project_settings = load_project_settings(plugin, args.project_settings_file)
    project_settings["output_directory"] = args.output_directory
    output_path = Path(args.output_directory)
    output_path.mkdir(exist_ok=True)

    LOG.info("Loading the resource provider definition...")
    resource_def = load_resource_spec(args.resource_def_file)
    LOG.info("Generating code...")
    plugin.generate(resource_def, project_settings)

    LOG.info("Generation complete.")


def setup_subparser(subparsers, parents):
    # see docstring of this file
    parser = subparsers.add_parser("generate", description=__doc__, parents=parents)
    parser.set_defaults(command=generate)
    parser.add_argument(
        "resource_def_file",
        type=TextFileType("r"),
        help="The resource provider definition to use for code generation.",
    )
    add_language_argument(parser)
    parser.add_argument(
        "--output-directory",
        dest="output_directory",
        default=os.getcwd(),
        help="Output directory for code generation. (Default: current directory)",
    )
    # we should always be able to provide some kind of default project setting,
    # so the user doesn't need to look these up before trying out codegen.
    parser.add_argument(
        "--project-settings",
        type=TextFileType("r"),
        default=None,
        dest="project_settings_file",
        help=(
            "The project settings to use for generation. "
            "These are language dependent. "
            "(Default: use default project settings)"
        ),
    )
