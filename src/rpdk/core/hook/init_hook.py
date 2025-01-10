import logging
import re

from rpdk.core.data_loaders import resource_json
from rpdk.core.exceptions import (
    CLIMisconfiguredError,
    WizardAbortError,
    WizardValidationError,
)
from rpdk.core.plugin_registry import get_plugin_choices
from rpdk.core.utils.init_utils import input_with_validation, print_error

LOG = logging.getLogger(__name__)
HOOK_TYPE_NAME_REGEX = r"^[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}$"
HOOK_PLUGINS = ("java", "python37", "python38", "python39")


def init_hook(args, project):
    if args.type_name:
        try:
            type_name = validate_type_name(args.type_name)
        except WizardValidationError as error:
            print_error(error)
            type_name = input_typename()
    else:
        type_name = input_typename()

    if "language" in vars(args):
        language = args.language.lower()
    else:
        language = input_language()

    settings = {
        arg: getattr(args, arg)
        for arg in vars(args)
        if not callable(getattr(args, arg))
    }

    project.init_hook(type_name, language, settings)
    try:
        project.generate(
            args.endpoint_url, args.region, False, args.target_schemas, args.profile
        )
    except CLIMisconfiguredError as e:
        LOG.debug(
            "Error when initializing hook project, attempting local project generation",
            exc_info=e,
        )
        example_target = resource_json(
            __name__, "../data/examples/hook/targets/aws-s3-bucket.json"
        )
        project.generate(
            args.endpoint_url, args.region, True, [example_target], args.profile
        )
    # Reload the generated example schema
    project.load_configuration_schema()
    # generate the docs based on the example schema loaded
    project.generate_docs()


def input_typename():
    type_name = input_with_validation(
        "What's the name of your hook type?",
        validate_type_name,
        "\n(Organization::Service::Hook)",
    )
    LOG.debug("Hook type identifier: %s", type_name)
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


def validate_type_name(value):
    match = re.match(HOOK_TYPE_NAME_REGEX, value)
    if match:
        return value
    LOG.debug("'%s' did not match '%s'", value, HOOK_TYPE_NAME_REGEX)
    raise WizardValidationError(
        f"Please enter a value matching '{HOOK_TYPE_NAME_REGEX}'"
    )


class ValidatePluginChoice:
    def __init__(self, choices):
        self.choices = tuple(
            filter(lambda hook_plugin: hook_plugin in HOOK_PLUGINS, choices)
        )
        self.max = len(self.choices)

        pretty = "\n".join(
            f"[{i}] {choice}" for i, choice in enumerate(self.choices, 1)
        )
        self.message = (
            "Select a language for code generation:\n"
            + pretty
            + "\n(enter an integer): "
        )

    def __call__(self, value):
        try:
            choice = int(value)
        except ValueError as e:
            raise WizardValidationError("Please enter an integer") from e
        choice -= 1
        if choice < 0 or choice >= self.max:
            raise WizardValidationError("Please select a choice")
        return self.choices[choice]


validate_plugin_choice = ValidatePluginChoice(  # pylint: disable=invalid-name
    get_plugin_choices()
)
