"""This sub command generates IDE and build files for a given language.
"""
import logging
import re
from functools import wraps

from colorama import Fore, Style

from .exceptions import WizardAbortError, WizardValidationError
from .plugin_registry import get_plugin_choices
from .project import Project

LOG = logging.getLogger(__name__)


TYPE_NAME_REGEX = r"^[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}$"


def input_with_validation(prompt, validate, description=""):
    while True:
        print(
            Style.BRIGHT,
            Fore.WHITE,
            prompt,
            Style.RESET_ALL,
            description,
            Style.RESET_ALL,
            sep="",
        )
        print(Fore.YELLOW, ">> ", Style.RESET_ALL, sep="", end="")
        response = input()
        try:
            return validate(response)
        except WizardValidationError as e:
            print(Style.BRIGHT, Fore.RED, str(e), Style.RESET_ALL, sep="")


def validate_type_name(value):
    match = re.match(TYPE_NAME_REGEX, value)
    if match:
        return value
    LOG.debug("'%s' did not match '%s'", value, TYPE_NAME_REGEX)
    raise WizardValidationError(
        "Please enter a resource type name matching '{}'".format(TYPE_NAME_REGEX)
    )


def validate_yes(value):
    return value.lower() in ("y", "yes")


class ValidatePluginChoice:
    def __init__(self, choices):
        self.choices = tuple(choices)
        self.max = len(self.choices)

        pretty = "\n".join(
            "[{}] {}".format(i, choice) for i, choice in enumerate(self.choices, 1)
        )
        self.message = (
            "Select a language for code generation:\n"
            + pretty
            + "\n(enter an integer): "
        )

    def __call__(self, value):
        try:
            choice = int(value)
        except ValueError:
            # pylint: disable=W0707
            raise WizardValidationError("Please enter an integer")
        choice -= 1
        if choice < 0 or choice >= self.max:
            raise WizardValidationError("Please select a choice")
        return self.choices[choice]


validate_plugin_choice = ValidatePluginChoice(  # pylint: disable=invalid-name
    get_plugin_choices()
)


def check_for_existing_project(project):
    try:
        project.load_settings()
    except FileNotFoundError:
        return  # good path

    if project.overwrite_enabled:
        LOG.warning("Overwriting settings file: %s", project.settings_path)
    else:
        LOG.debug(
            "Settings file for '%s' already exists: %s",
            project.type_name,
            project.settings_path,
        )
        project.overwrite_enabled = input_with_validation(
            "Found existing settings - overwrite (y/N)?",
            validate_yes,
            "\n" + project.type_name,
        )
        LOG.debug("Overwrite response: %s", project.overwrite_enabled)
        if not project.overwrite_enabled:
            raise WizardAbortError()


def input_typename():
    type_name = input_with_validation(
        "What's the name of your resource type?",
        validate_type_name,
        "\n(Organization::Service::Resource)",
    )
    LOG.debug("Resource type identifier: %s", type_name)
    return type_name


def input_language():
    # language/plugin
    if validate_plugin_choice.max < 1:
        LOG.critical("No language plugins found")
        raise WizardAbortError()

    if validate_plugin_choice.max == 1:
        language = validate_plugin_choice.choices[0]
        LOG.warning("One language plugin found, defaulting to %s", language)
    else:
        language = input_with_validation(
            validate_plugin_choice.message, validate_plugin_choice
        )
    LOG.debug("Language plugin: %s", language)
    return language


def init(args):
    project = Project(args.force)

    LOG.warning("Initializing new project")

    check_for_existing_project(project)

    if args.type_name:
        try:
            type_name = validate_type_name(args.type_name)
        except WizardValidationError as error:
            print(Style.BRIGHT, Fore.RED, str(error), Style.RESET_ALL, sep="")
            type_name = input_typename()
    else:
        type_name = input_typename()

    if args.language:
        language = args.language.lower()
        if language not in get_plugin_choices():
            print(
                Style.BRIGHT,
                Fore.RED,
                "The plugin for {} is not installed.".format(language),
                Style.RESET_ALL,
                sep="",
            )
            language = input_language()
    else:
        language = input_language()

    project.init(
        type_name,
        language,
        {
            "use_docker": args.use_docker,
            "namespace": args.namespace,
            "codegen_template_path": args.codegen_model,
            "importpath": args.import_path,
        },
    )
    project.generate()
    project.generate_docs()

    LOG.warning("Initialized a new project in %s", project.root.resolve())


def ignore_abort(function):
    @wraps(function)
    def wrapper(args):
        try:
            function(args)
        except (KeyboardInterrupt, WizardAbortError):
            print("\naborted")
            # pylint: disable=W0707
            raise SystemExit(1)

    return wrapper


def setup_subparser(subparsers, parents):
    # see docstring of this file
    parser = subparsers.add_parser("init", description=__doc__, parents=parents)
    parser.set_defaults(command=ignore_abort(init))

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force files to be overwritten.",
    )

    parser.add_argument(
        "--language",
        help="""Select a language for code generation.
            The language plugin needs to be installed.""",
    )

    parser.add_argument(
        "--type-name",
        help="Select the name of the resource type.",
    )

    parser.add_argument(
        "--use-docker",
        action="store_true",
        help="""Use docker for python platform-independent packaging.
            This is highly recommended unless you are experienced
            with cross-platform Python packaging.""",
    )

    parser.add_argument(
        "--namespace",
        nargs="?",
        const="default",
        help="""Select the name of the Java namespace.
            Passing the flag without argument select the default namespace.""",
    )

    parser.add_argument(
        "--codegen-model",
        choices=["default", "guided_aws"],
        help="Select a codegen model.",
    )

    parser.add_argument(
        "--import-path",
        help="Select the go language import path.",
    )
