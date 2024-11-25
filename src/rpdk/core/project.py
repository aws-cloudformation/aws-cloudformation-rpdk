# pylint: disable=too-many-lines
import json
import logging
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any, Dict
from uuid import uuid4

import jsonpatch
import yaml
from botocore.exceptions import ClientError, WaiterError
from jinja2 import Environment, PackageLoader, select_autoescape
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError

from rpdk.core.fragment.generator import TemplateFragment
from rpdk.core.jsonutils.flattener import JsonSchemaFlattener

from . import __version__
from .boto_helpers import create_sdk_session
from .data_loaders import load_hook_spec, load_resource_spec, resource_json
from .exceptions import (
    DownstreamError,
    FragmentValidationError,
    InternalError,
    InvalidProjectError,
    RPDKBaseException,
    SpecValidationError,
)
from .fragment.module_fragment_reader import _get_fragment_file
from .jsonutils.pointer import fragment_decode, fragment_encode
from .jsonutils.utils import traverse
from .plugin_registry import load_plugin
from .type_name_resolver import TypeNameResolver
from .type_schema_loader import TypeSchemaLoader
from .upload import Uploader

LOG = logging.getLogger(__name__)

SETTINGS_FILENAME = ".rpdk-config"
SCHEMA_UPLOAD_FILENAME = "schema.json"
CONFIGURATION_SCHEMA_UPLOAD_FILENAME = "configuration-schema.json"
OVERRIDES_FILENAME = "overrides.json"
TARGET_INFO_FILENAME = "target-info.json"
INPUTS_FOLDER = "inputs"
EXAMPLE_INPUTS_FOLDER = "example_inputs"
TARGET_SCHEMAS_FOLDER = "target-schemas"
HOOK_ROLE_TEMPLATE_FILENAME = "hook-role.yaml"
RESOURCE_ROLE_TEMPLATE_FILENAME = "resource-role.yaml"
TYPE_NAME_RESOURCE_REGEX = "^[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}$"
TYPE_NAME_MODULE_REGEX = (
    "^[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}::MODULE$"
)
TYPE_NAME_HOOK_REGEX = "^[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}$"
ARTIFACT_TYPE_RESOURCE = "RESOURCE"
ARTIFACT_TYPE_MODULE = "MODULE"
ARTIFACT_TYPE_HOOK = "HOOK"
TARGET_CANARY_ROOT_FOLDER = "canary-bundle"
TARGET_CANARY_FOLDER = "canary-bundle/canary"
RPDK_CONFIG_FILE = ".rpdk-config"
CANARY_FILE_PREFIX = "canary"
CANARY_FILE_CREATE_SUFFIX = "001"
CANARY_FILE_UPDATE_SUFFIX = "002"
CANARY_SUPPORTED_PATCH_INPUT_OPERATIONS = {"replace", "remove", "add"}
CREATE_INPUTS_KEY = "CreateInputs"
PATCH_INPUTS_KEY = "PatchInputs"
PATCH_VALUE_KEY = "value"
PATCH_OPERATION_KEY = "op"
CONTRACT_TEST_DEPENDENCY_FILE_NAME = "dependencies.yml"
CANARY_DEPENDENCY_FILE_NAME = "bootstrap.yaml"
CANARY_SETTINGS = "canarySettings"
TYPE_NAME = "typeName"
CONTRACT_TEST_FILE_NAMES = "contract_test_file_names"
INPUT1_FILE_NAME = "inputs_1.json"
CONTRACT_TEST_FOLDER = "contract-tests-artifacts"
CONTRACT_TEST_INPUT_PREFIX = "inputs_*"
CONTRACT_TEST_DEPENDENCY_FILE_NAME = "dependencies.yml"
TYPE_NAME = "typeName"
CONTRACT_TEST_FILE_NAMES = "contract_test_file_names"
FN_SUB = "Fn::Sub"
FN_IMPORT_VALUE = "Fn::ImportValue"
UUID = "uuid"
DYNAMIC_VALUES_MAP = {
    "region": "${AWS::Region}",
    "partition": "${AWS::Partition}",
    "account": "${AWS::AccountId}",
}
DEFAULT_ROLE_TIMEOUT_MINUTES = 120  # 2 hours
# min and max are according to CreateRole API restrictions
# https://docs.aws.amazon.com/IAM/latest/APIReference/API_CreateRole.html
MIN_ROLE_TIMEOUT_SECONDS = 3600  # 1 hour
MAX_ROLE_TIMEOUT_SECONDS = 43200  # 12 hours
MAX_RPDK_CONFIG_LENGTH = 10 * 1024  # 10 KiB
MAX_CONFIGURATION_SCHEMA_LENGTH = 60 * 1024  # 60 KiB

PROTOCOL_VERSION_VALUES = frozenset({"1.0.0", "2.0.0"})

CFN_METADATA_FILENAME = ".cfn_metadata.json"

SETTINGS_VALIDATOR = Draft7Validator(
    {
        "properties": {
            "artifact_type": {"type": "string"},
            "language": {"type": "string"},
            "typeName": {"type": "string", "pattern": TYPE_NAME_RESOURCE_REGEX},
            "runtime": {"type": "string"},
            "entrypoint": {"type": ["string", "null"]},
            "testEntrypoint": {"type": ["string", "null"]},
            "executableEntrypoint": {"type": ["string", "null"]},
            "settings": {"type": "object"},
        },
        "required": ["language", "typeName", "runtime", "entrypoint"],
    }
)

MODULE_SETTINGS_VALIDATOR = Draft7Validator(
    {
        "properties": {
            "artifact_type": {"type": "string"},
            "typeName": {"type": "string", "pattern": TYPE_NAME_MODULE_REGEX},
            "settings": {"type": "object"},
        },
        "required": ["artifact_type", "typeName"],
    }
)

HOOK_SETTINGS_VALIDATOR = Draft7Validator(
    {
        "properties": {
            "artifact_type": {"type": "string"},
            "language": {"type": "string"},
            "typeName": {"type": "string", "pattern": TYPE_NAME_HOOK_REGEX},
            "runtime": {"type": "string"},
            "entrypoint": {"type": ["string", "null"]},
            "testEntrypoint": {"type": ["string", "null"]},
            "settings": {"type": "object"},
        },
        "required": ["language", "typeName", "runtime", "entrypoint"],
    }
)

BASIC_TYPE_MAPPINGS = {
    "string": "String",
    "number": "Double",
    "integer": "Integer",
    "boolean": "Boolean",
}

MARKDOWN_RESERVED_CHARACTERS = frozenset({"^", "*", "+", ".", "(", "[", "{", "#"})

HOOK_SPECIAL_TARGET_NAMES = frozenset(("STACK", "CHANGE_SET"))


def escape_markdown(string):
    """Escapes the reserved Markdown characters."""
    if not string:
        return string
    if string[0] in MARKDOWN_RESERVED_CHARACTERS:
        return f"\\{string}"
    return string


class Project:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    def __init__(self, overwrite_enabled=False, root=None):
        self.overwrite_enabled = overwrite_enabled
        self.root = Path(root) if root else Path.cwd()
        self.settings_path = self.root / SETTINGS_FILENAME
        self.type_info = None
        self.artifact_type = None
        self.language = None
        self._plugin = None
        self.settings = None
        self.schema = None
        self.configuration_schema = None
        self._flattened_schema = None
        self._marked_down_properties = {}
        self.runtime = "noexec"
        self.entrypoint = None
        self.test_entrypoint = None
        self.executable_entrypoint = None
        self.fragment_dir = None
        self.canary_settings = {}
        self.target_info = {}

        self.env = Environment(
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            loader=PackageLoader(__name__, "templates/"),
            autoescape=select_autoescape(["html", "htm", "xml", "md"]),
        )

        self.env.filters["escape_markdown"] = escape_markdown

        LOG.debug("Root directory: %s", self.root)

    @property
    def type_name(self):
        return "::".join(self.type_info)

    @type_name.setter
    def type_name(self, value):
        self.type_info = tuple(value.split("::"))

    @property
    def hypenated_name(self):
        return "-".join(self.type_info).lower()

    @property
    def hyphenated_name_case_sensitive(self):
        return "-".join(self.type_info)

    @property
    def schema_filename(self):
        return f"{self.hypenated_name}.json"

    @property
    def configuration_schema_filename(self):
        return f"{self.hypenated_name}-configuration.json"

    @property
    def schema_path(self):
        return self.root / self.schema_filename

    @property
    def overrides_path(self):
        return self.root / OVERRIDES_FILENAME

    @property
    def inputs_path(self):
        return self.root / INPUTS_FOLDER

    @property
    def example_inputs_path(self):
        return self.root / EXAMPLE_INPUTS_FOLDER

    @property
    def target_schemas_path(self):
        return self.root / TARGET_SCHEMAS_FOLDER

    @property
    def target_info_path(self):
        return self.root / TARGET_INFO_FILENAME

    @property
    def target_canary_root_path(self):
        return self.root / TARGET_CANARY_ROOT_FOLDER

    @property
    def target_canary_folder_path(self):
        return self.root / TARGET_CANARY_FOLDER

    @property
    def rpdk_config(self):
        return self.root / RPDK_CONFIG_FILE

    @property
    def file_generation_enabled(self):
        if self.canary_settings == {}:
            return False
        return True

    @property
    def contract_test_file_names(self):
        return self.canary_settings.get(CONTRACT_TEST_FILE_NAMES, [INPUT1_FILE_NAME])

    @property
    def target_contract_test_folder_path(self):
        return self.root / CONTRACT_TEST_FOLDER

    @staticmethod
    def _raise_invalid_project(msg, e):
        LOG.debug(msg, exc_info=e)
        raise InvalidProjectError(msg) from e

    def load_settings(self):
        LOG.debug("Loading project file '%s'", self.settings_path)
        try:
            with self.settings_path.open("r", encoding="utf-8") as f:
                raw_settings = json.load(f)
        except json.JSONDecodeError as e:
            self._raise_invalid_project(
                f"Project file '{self.settings_path}' is invalid", e
            )

        # check size of RPDK config
        if len(json.dumps(raw_settings).encode("utf-8")) > MAX_RPDK_CONFIG_LENGTH:
            raise InvalidProjectError(
                f"Project file '{self.settings_path}' exceeds maximum length of 10 KiB."
            )
        # validate protocol version, if specified
        if "settings" in raw_settings and "protocolVersion" in raw_settings["settings"]:
            protocol_version = raw_settings["settings"]["protocolVersion"]
            if protocol_version not in PROTOCOL_VERSION_VALUES:
                raise InvalidProjectError(
                    f"Invalid 'protocolVersion' settings in '{self.settings_path}"
                )
        else:
            LOG.warning(
                "No protovolVersion found: this will default to version 1.0.0 during registration. "
                "Please consider upgrading to CFN-CLI 2.0 following the guide: "
                "https://docs.aws.amazon.com/cloudformation-cli/latest/userguide/what-is-cloudformation-cli.html"
            )

        # backward compatible
        if "artifact_type" not in raw_settings:
            raw_settings["artifact_type"] = ARTIFACT_TYPE_RESOURCE

        if raw_settings["artifact_type"] == ARTIFACT_TYPE_RESOURCE:
            self.validate_and_load_resource_settings(raw_settings)
        elif raw_settings["artifact_type"] == ARTIFACT_TYPE_HOOK:
            self.validate_and_load_hook_settings(raw_settings)
        else:
            self.validate_and_load_module_settings(raw_settings)

    def validate_and_load_hook_settings(self, raw_settings):
        try:
            HOOK_SETTINGS_VALIDATOR.validate(raw_settings)
        except ValidationError as e:
            self._raise_invalid_project(
                f"Project file '{self.settings_path}' is invalid", e
            )
        self.type_name = raw_settings["typeName"]
        self.artifact_type = raw_settings["artifact_type"]
        self.language = raw_settings["language"]
        self.runtime = raw_settings["runtime"]
        self.entrypoint = raw_settings["entrypoint"]
        self.test_entrypoint = raw_settings["testEntrypoint"]
        self.executable_entrypoint = raw_settings.get("executableEntrypoint")
        self._plugin = load_plugin(raw_settings["language"])
        self.settings = raw_settings.get("settings", {})

    def validate_and_load_module_settings(self, raw_settings):
        try:
            MODULE_SETTINGS_VALIDATOR.validate(raw_settings)
        except ValidationError as e:
            self._raise_invalid_project(
                f"Project file '{self.settings_path}' is invalid", e
            )
        self.type_name = raw_settings["typeName"]
        self.artifact_type = raw_settings["artifact_type"]
        self.settings = raw_settings.get("settings", {})

    def validate_and_load_resource_settings(self, raw_settings):
        try:
            SETTINGS_VALIDATOR.validate(raw_settings)
        except ValidationError as e:
            self._raise_invalid_project(
                f"Project file '{self.settings_path}' is invalid", e
            )
        self.type_name = raw_settings["typeName"]
        self.artifact_type = raw_settings["artifact_type"]
        self.language = raw_settings["language"]
        self.runtime = raw_settings["runtime"]
        self.entrypoint = raw_settings["entrypoint"]
        self.test_entrypoint = raw_settings["testEntrypoint"]
        self.executable_entrypoint = raw_settings.get("executableEntrypoint")
        self._plugin = load_plugin(raw_settings["language"])
        self.settings = raw_settings.get("settings", {})
        self.canary_settings = raw_settings.get("canarySettings", {})

    def _write_example_schema(self):
        self.schema = resource_json(
            __name__, "data/examples/resource/initech.tps.report.v1.json"
        )
        self.schema["typeName"] = self.type_name

        def _write(f):
            json.dump(self.schema, f, indent=4)
            f.write("\n")

        self.safewrite(self.schema_path, _write)

    def _write_example_hook_schema(self):
        self.schema = resource_json(
            __name__, "data/examples/hook/sse.verification.v1.json"
        )
        self.schema["typeName"] = self.type_name

        def _write(f):
            json.dump(self.schema, f, indent=4)
            f.write("\n")

        self.safewrite(self.schema_path, _write)

    def _write_example_inputs(self):
        shutil.rmtree(self.example_inputs_path, ignore_errors=True)
        self.example_inputs_path.mkdir(exist_ok=True)

        template = self.env.get_template("inputs.json")
        properties = list(self.schema["properties"].keys())

        for inputs_file in (
            "inputs_1_create.json",
            "inputs_1_update.json",
            "inputs_1_invalid.json",
        ):
            self.safewrite(
                self.example_inputs_path / inputs_file,
                template.render(
                    properties=properties[:-1], last_property=properties[-1]
                ),
            )

    def write_settings(self):
        def _write_resource_settings(f):
            executable_entrypoint_dict = (
                {"executableEntrypoint": self.executable_entrypoint}
                if self.executable_entrypoint
                else {}
            )
            json.dump(
                {
                    "artifact_type": self.artifact_type,
                    "typeName": self.type_name,
                    "language": self.language,
                    "runtime": self.runtime,
                    "entrypoint": self.entrypoint,
                    "testEntrypoint": self.test_entrypoint,
                    "settings": self.settings,
                    **executable_entrypoint_dict,
                    "canarySettings": self.canary_settings,
                },
                f,
                indent=4,
            )
            f.write("\n")

        def _write_module_settings(f):
            json.dump(
                {
                    "artifact_type": self.artifact_type,
                    "typeName": self.type_name,
                    "settings": self.settings,
                },
                f,
                indent=4,
            )
            f.write("\n")

        def _write_hook_settings(f):
            executable_entrypoint_dict = (
                {"executableEntrypoint": self.executable_entrypoint}
                if self.executable_entrypoint
                else {}
            )
            json.dump(
                {
                    "artifact_type": self.artifact_type,
                    "typeName": self.type_name,
                    "language": self.language,
                    "runtime": self.runtime,
                    "entrypoint": self.entrypoint,
                    "testEntrypoint": self.test_entrypoint,
                    "settings": self.settings,
                    **executable_entrypoint_dict,
                },
                f,
                indent=4,
            )
            f.write("\n")

        if self.artifact_type == ARTIFACT_TYPE_RESOURCE:
            self.overwrite(self.settings_path, _write_resource_settings)
        elif self.artifact_type == ARTIFACT_TYPE_HOOK:
            self.overwrite(self.settings_path, _write_hook_settings)
        else:
            self.overwrite(self.settings_path, _write_module_settings)

    def init(self, type_name, language, settings=None):
        self.artifact_type = ARTIFACT_TYPE_RESOURCE
        self.type_name = type_name
        self.language = language
        self._plugin = load_plugin(language)
        self.settings = settings or {}
        self.canary_settings = {
            CONTRACT_TEST_FILE_NAMES: [INPUT1_FILE_NAME],
        }
        self._write_example_schema()
        self._write_example_inputs()
        self._plugin.init(self)
        self.write_settings()

    def init_module(self, type_name):
        self.artifact_type = ARTIFACT_TYPE_MODULE
        self.type_name = type_name
        self.settings = {}
        self.write_settings()

    def init_hook(self, type_name, language, settings=None):
        self.artifact_type = ARTIFACT_TYPE_HOOK
        self.type_name = type_name
        self.language = language
        self._plugin = load_plugin(language)
        self.settings = settings or {}
        self._write_example_hook_schema()
        self._plugin.init(self)
        self.write_settings()

    def load_hook_schema(self):
        if not self.type_info:
            msg = "Internal error (Must load settings first)"
            LOG.critical(msg)
            raise InternalError(msg)

        with self.schema_path.open("r", encoding="utf-8") as f:
            self.schema = load_hook_spec(f)

    def load_schema(self):
        if not self.type_info:
            msg = "Internal error (Must load settings first)"
            LOG.critical(msg)
            raise InternalError(msg)

        with self.schema_path.open("r", encoding="utf-8") as f:
            self.schema = load_resource_spec(f)

    def load_configuration_schema(self):
        if not self.schema:
            msg = "Internal error (Must load type schema first)"
            LOG.critical(msg)
            raise InternalError(msg)

        if "typeConfiguration" in self.schema:
            configuration_schema = self.schema["typeConfiguration"]
            configuration_schema["definitions"] = self.schema.get("definitions", {})
            configuration_schema["typeName"] = self.type_name
            self.configuration_schema = configuration_schema

    def write_configuration_schema(self, path):
        LOG.debug(
            "Writing type configuration resource specification from resource"
            " specification: %s",
            path,
        )

        def _write(f):
            json.dump(self.configuration_schema, f, indent=4)
            f.write("\n")

        self.overwrite(path, _write)

    @staticmethod
    def overwrite(path, contents):
        LOG.debug("Overwriting '%s'", path)
        with path.open("w", encoding="utf-8") as f:
            if callable(contents):
                contents(f)
            else:
                f.write(contents)

    def safewrite(self, path, contents):
        if self.overwrite_enabled:
            self.overwrite(path, contents)
        else:
            try:
                with path.open("x", encoding="utf-8") as f:
                    if callable(contents):
                        contents(f)
                    else:
                        f.write(contents)
            except FileExistsError:
                LOG.info("File already exists, not overwriting '%s'", path)

    def generate(
        self,
        endpoint_url=None,
        region_name=None,
        local_only=False,
        target_schemas=None,
        profile_name=None,
    ):  # pylint: disable=too-many-arguments
        if self.artifact_type == ARTIFACT_TYPE_MODULE:
            return  # for Modules, the schema is already generated in cfn validate

        # generate template for IAM role assumed by cloudformation
        # to provision resources if schema has handlers defined
        if "handlers" in self.schema:
            handlers = self.schema["handlers"]
            permission = "Allow"
            if self.artifact_type == ARTIFACT_TYPE_HOOK:
                template = self.env.get_template("hook-role.yml")
                path = self.root / HOOK_ROLE_TEMPLATE_FILENAME
            else:
                template = self.env.get_template("resource-role.yml")
                path = self.root / RESOURCE_ROLE_TEMPLATE_FILENAME
            LOG.debug("Writing Execution Role CloudFormation template: %s", path)
            actions = {
                action
                for handler in handlers.values()
                for action in handler.get("permissions", [])
            }

            # calculate IAM role max session timeout based on highest handler timeout
            # with some buffer (70 seconds per minute)
            max_handler_timeout = max(
                (
                    handler.get("timeoutInMinutes", DEFAULT_ROLE_TIMEOUT_MINUTES)
                    for operation, handler in handlers.items()
                ),
                default=DEFAULT_ROLE_TIMEOUT_MINUTES,
            )
            # max role session timeout must be between 1 hour and 12 hours
            role_session_timeout = min(
                MAX_ROLE_TIMEOUT_SECONDS,
                max(MIN_ROLE_TIMEOUT_SECONDS, 70 * max_handler_timeout),
            )

            # gets rid of any empty string actions.
            # Empty strings cannot be specified as an action in an IAM statement
            actions.discard("")
            # Check if handler has actions
            if not actions:
                actions.add("*")
                permission = "Deny"

            contents = template.render(
                type_name=self.hyphenated_name_case_sensitive,
                actions=sorted(actions),
                permission=permission,
                role_session_timeout=role_session_timeout,
            )
            self.overwrite(path, contents)
            self.target_info = self._load_target_info(
                endpoint_url,
                region_name,
                type_schemas=target_schemas,
                local_only=local_only,
                profile_name=profile_name,
            )

        self._plugin.generate(self)

    def load(self):
        try:
            self.load_settings()
        except FileNotFoundError as e:
            self._raise_invalid_project(
                f"Project file {self.settings_path} not found. Have you run 'init' or"
                " in a wrong directory?",
                e,
            )

        if self.artifact_type == ARTIFACT_TYPE_MODULE:
            self._load_modules_project()
        elif self.artifact_type == ARTIFACT_TYPE_HOOK:
            self._load_hooks_project()
        else:
            self._load_resources_project()

    def _load_resources_project(self):
        LOG.info("Validating your resource specification...")
        try:
            self.load_schema()
            self.load_configuration_schema()
            LOG.warning("Resource schema is valid.")
        except FileNotFoundError as e:
            self._raise_invalid_project("Resource schema not found.", e)
        except SpecValidationError as e:
            msg = "Resource schema is invalid: " + str(e)
            self._raise_invalid_project(msg, e)
        LOG.info("Validating your resource schema...")

    def _load_modules_project(self):
        LOG.info("Validating your module fragments...")
        template_fragment = TemplateFragment(self.type_name, self.root)
        try:
            self._validate_fragments(template_fragment)
        except FragmentValidationError as e:
            msg = "Invalid template fragment: " + str(e)
            self._raise_invalid_project(msg, e)
        self.schema = template_fragment.generate_schema()
        self.fragment_dir = template_fragment.fragment_dir

    def _load_hooks_project(self):
        LOG.info("Validating your hook specification...")
        try:
            self.load_hook_schema()
            self.load_configuration_schema()
        except FileNotFoundError as e:
            self._raise_invalid_project("Hook specification not found.", e)
        except SpecValidationError as e:
            msg = "Hook specification is invalid: " + str(e)
            self._raise_invalid_project(msg, e)

    def _add_modules_content_to_zip(self, zip_file):
        if not os.path.exists(self.root / SCHEMA_UPLOAD_FILENAME):
            msg = "Module schema could not be found"
            raise InternalError(msg)
        zip_file.write(self.root / SCHEMA_UPLOAD_FILENAME, SCHEMA_UPLOAD_FILENAME)
        file = _get_fragment_file(self.fragment_dir)
        zip_file.write(
            file,
            arcname=file.replace(str(self.fragment_dir), "fragments/"),
        )

    @staticmethod
    def _validate_fragments(template_fragment):
        template_fragment.validate_fragments()

    def submit(
        self,
        dry_run,
        endpoint_url,
        region_name,
        role_arn,
        use_role,
        set_default,
        profile_name,
    ):  # pylint: disable=too-many-arguments
        context_mgr = self._create_context_manager(dry_run)

        with context_mgr as f:
            # the default compression is ZIP_STORED, which helps with the
            # file-size check on upload
            args = {}
            if sys.version_info >= (3, 8):
                args = {"strict_timestamps": False}
            with zipfile.ZipFile(f, mode="w", **args) as zip_file:
                if self.configuration_schema:
                    with zip_file.open(
                        CONFIGURATION_SCHEMA_UPLOAD_FILENAME, "w"
                    ) as configuration_file:
                        configuration_file.write(
                            json.dumps(self.configuration_schema, indent=4).encode(
                                "utf-8"
                            )
                        )
                zip_file.write(self.settings_path, SETTINGS_FILENAME)
                if self.artifact_type == ARTIFACT_TYPE_MODULE:
                    self._add_modules_content_to_zip(zip_file)
                elif self.artifact_type == ARTIFACT_TYPE_HOOK:
                    self._add_hooks_content_to_zip(
                        zip_file, endpoint_url, region_name, profile_name
                    )
                else:
                    self._add_resources_content_to_zip(zip_file)

                self._add_overrides_file_to_zip(zip_file)

            if dry_run:
                LOG.error("Dry run complete: %s", self._get_zip_file_path().resolve())
            else:
                f.seek(0)
                self._upload(
                    f,
                    endpoint_url,
                    region_name,
                    role_arn,
                    use_role,
                    set_default,
                    profile_name,
                )

    def _add_overrides_file_to_zip(self, zip_file):
        try:
            zip_file.write(self.overrides_path, OVERRIDES_FILENAME)
            LOG.debug("%s found. Writing to package.", OVERRIDES_FILENAME)
        except FileNotFoundError:
            LOG.debug("%s not found. Not writing to package.", OVERRIDES_FILENAME)

    def _add_resources_content_to_zip(self, zip_file):
        zip_file.write(self.schema_path, SCHEMA_UPLOAD_FILENAME)
        if os.path.isdir(self.inputs_path):
            for filename in os.listdir(self.inputs_path):
                absolute_path = self.inputs_path / filename
                zip_file.write(absolute_path, INPUTS_FOLDER + "/" + filename)
                LOG.debug("%s found. Writing to package.", filename)
        else:
            LOG.debug("%s not found. Not writing to package.", INPUTS_FOLDER)
        self._plugin.package(self, zip_file)
        cli_metadata = {}
        try:
            cli_metadata = self._plugin.get_plugin_information(self)
        except AttributeError:
            LOG.debug(
                "Version info is not available for plugins, not writing to metadata"
                " file"
            )
        cli_metadata["cli-version"] = __version__
        zip_file.writestr(CFN_METADATA_FILENAME, json.dumps(cli_metadata))

    def _add_hooks_content_to_zip(
        self, zip_file, endpoint_url=None, region_name=None, profile_name=None
    ):
        zip_file.write(self.schema_path, SCHEMA_UPLOAD_FILENAME)
        if os.path.isdir(self.inputs_path):
            for filename in os.listdir(self.inputs_path):
                absolute_path = self.inputs_path / filename
                zip_file.write(absolute_path, INPUTS_FOLDER + "/" + filename)
                LOG.debug("%s found. Writing to package.", filename)
        else:
            LOG.debug("%s not found. Not writing to package.", INPUTS_FOLDER)

        target_info = {}
        try:
            target_info = (
                self.target_info
                if self.target_info
                else self._load_target_info(
                    endpoint_url, region_name, profile_name=profile_name
                )
            )
        except RPDKBaseException as e:
            LOG.warning("Failed to load target info, attempting local...", exc_info=e)
            try:
                target_info = self._load_target_info(None, None, local_only=True)
            except RPDKBaseException as ex:
                LOG.warning("Failed to load target info, ignoring...", exc_info=ex)

        if target_info:
            zip_file.writestr(TARGET_INFO_FILENAME, json.dumps(target_info, indent=4))
        for target_name, info in target_info.items():
            filename = f'{"-".join(s.lower() for s in target_name.split("::"))}.json'
            content = json.dumps(info.get("Schema", {}), indent=4).encode("utf-8")
            zip_file.writestr(TARGET_SCHEMAS_FOLDER + "/" + filename, content)
            LOG.debug("%s found. Writing to package.", filename)

        self._plugin.package(self, zip_file)
        cli_metadata = {}
        try:
            cli_metadata = self._plugin.get_plugin_information(self)
        except AttributeError:
            LOG.debug(
                "Version info is not available for plugins, not writing to metadata"
                " file"
            )
        cli_metadata["cli-version"] = __version__
        zip_file.writestr(CFN_METADATA_FILENAME, json.dumps(cli_metadata))

    # pylint: disable=R1732
    def _create_context_manager(self, dry_run):
        # if it's a dry run, keep the file; otherwise can delete after upload
        if dry_run:
            return self._get_zip_file_path().open("wb")

        return TemporaryFile("w+b")

    def _get_zip_file_path(self):
        return Path(f"{self.hypenated_name}.zip")

    def generate_docs(self):
        if self.artifact_type == ARTIFACT_TYPE_MODULE:
            return

        # generate the docs folder that contains documentation based on the schema
        docs_path = self.root / "docs"

        docs_attribute = (
            self.configuration_schema
            if self.artifact_type == ARTIFACT_TYPE_HOOK
            else self.schema
        )
        if (
            not self.type_info
            or not docs_attribute
            or "properties" not in docs_attribute
        ):
            LOG.warning(
                "Could not generate schema docs due to missing type info or schema"
            )
            return

        target_names = (
            self.target_info.keys()
            if self.target_info
            else (
                {
                    target_name
                    for handler in self.schema.get("handlers", {}).values()
                    for target_name in handler.get("targetNames", [])
                }
                if self.artifact_type == ARTIFACT_TYPE_HOOK
                else []
            )
        )

        LOG.debug("Removing generated docs: %s", docs_path)
        shutil.rmtree(docs_path, ignore_errors=True)
        docs_path.mkdir(exist_ok=True)

        LOG.debug("Writing generated docs")

        # take care not to modify the master schema
        docs_schema = json.loads(json.dumps(docs_attribute))
        self._flattened_schema = JsonSchemaFlattener(
            json.loads(json.dumps(docs_attribute))
        ).flatten_schema()

        docs_schema["properties"] = {
            name: self._set_docs_properties(name, value, (name,))
            for name, value in self._flattened_schema[()]["properties"].items()
        }

        LOG.debug("Finished documenting nested properties")

        ref = self._get_docs_primary_identifier(docs_schema)
        getatt = self._get_docs_gettable_atts(docs_schema)

        readme_path = docs_path / "README.md"
        LOG.debug("Writing docs README: %s", readme_path)
        readme_template = (
            "hook-docs-readme.md"
            if self.artifact_type == ARTIFACT_TYPE_HOOK
            else "docs-readme.md"
        )
        template = self.env.get_template(readme_template)
        contents = template.render(
            type_name=self.type_name,
            schema=docs_schema,
            ref=ref,
            getatt=getatt,
            target_names=sorted(target_names),
        )
        self.safewrite(readme_path, contents)

    def generate_image_build_config(self):
        if not hasattr(self._plugin, "generate_image_build_config"):
            raise InvalidProjectError(
                f"Plugin for the {self.runtime} runtime does not support building an"
                " image"
            )
        return self._plugin.generate_image_build_config(self)

    @staticmethod
    def _get_docs_primary_identifier(docs_schema):
        try:
            primary_id = docs_schema["primaryIdentifier"]
            if len(primary_id) == 1:
                # drop /properties
                primary_id_path = fragment_decode(primary_id[0], prefix="")[1:]
                # at some point, someone might use a nested primary ID
                if len(primary_id_path) == 1:
                    return primary_id_path[0]
                LOG.warning("Nested primaryIdentifier found")
        except (KeyError, ValueError):
            pass
        return None

    @staticmethod
    def _get_docs_gettable_atts(docs_schema):
        def _get_property_description(prop):
            path = fragment_decode(prop, prefix="")
            name = path[-1]
            try:
                desc, _resolved_path, _parent = traverse(
                    docs_schema, path + ("description",)
                )
            except (KeyError, IndexError, ValueError):
                desc = f"Returns the <code>{name}</code> value."
            return {"name": name, "description": desc}

        return [
            _get_property_description(prop)
            for prop in docs_schema.get("readOnlyProperties", [])
        ]

    def _set_docs_properties(  # noqa: C901
        self, propname, prop, proppath
    ):  # pylint: disable=too-many-locals,too-many-statements
        """method sets markdown for each property;
        1. Supports multiple types per property - done via flattened schema so `allOf`,
        `anyOf`, `oneOf` combined into a collection then method iterates to reapply
        itself to each type
        2. Supports circular reference - done via pre calculating hypothetical .md file
        path and name
        which is reused once property is hit more than once

        Args:
            propname ([str]): property name
            prop ([dict]): all the sub propeties
            proppath ([tuple]): path of the property

        Returns:
            [dict]: modified sub dictionary with attached markdown
        """
        types = ("jsontype", "yamltype", "longformtype")
        jsontype, yamltype, longformtype = types

        # reattach prop from reference
        if "$ref" in prop:
            ref = self._flattened_schema[prop["$ref"]]
            propname = prop["$ref"][1]
            # this is to tie object to a definition and not to a property
            proppath = (propname,)
            prop = ref

        # this means method is traversing already visited property
        if propname in self._marked_down_properties:
            return {
                property_item: markdown
                for property_item, markdown in self._marked_down_properties[
                    propname
                ].items()
                if property_item in types
            }  # returning already set markdown

        proppath_ptr = fragment_encode(("properties",) + proppath, prefix="")
        if (
            "createOnlyProperties" in self.schema
            and proppath_ptr in self.schema["createOnlyProperties"]
        ):
            prop["createonly"] = True
        if (
            "conditionalCreateOnlyProperties" in self.schema
            and proppath_ptr in self.schema["conditionalCreateOnlyProperties"]
        ):
            prop["conditionalCreateOnly"] = True
        if (
            "readOnlyProperties" in self.schema
            and proppath_ptr in self.schema["readOnlyProperties"]
        ):
            prop["readonly"] = True

        # join multiple types
        def __join(item1, item2):
            if not item1 or item2 == item1:
                return item2
            return f"{item1}, {item2}"

        def __set_property_type(prop_type, single_type=True):
            nonlocal prop

            # mark down formatting of the target value - used for complex objects
            # ($ref) and arrays of such objects
            # pylint: disable=unnecessary-lambda-assignment
            markdown_lambda = (
                lambda fname, name: f'<a href="{fname}">{name}</a>'  # noqa: B950, C0301
            )

            type_json = type_yaml = type_longform = "Unknown"
            if prop_type in BASIC_TYPE_MAPPINGS:
                # primitives should not occur for circular ref;
                type_json = type_yaml = type_longform = BASIC_TYPE_MAPPINGS[prop_type]
            elif prop_type == "array":
                # lambdas to reuse formatting
                markdown_json = (
                    lambda markdown_value: f"[ {markdown_value}, ... ]"
                )  # noqa: E731
                markdown_yaml = lambda markdown_value: (  # noqa: E731
                    f"\n      - {markdown_value}"
                    if single_type
                    else f"[ {markdown_value}, ... ]"
                )
                markdown_long = (
                    lambda markdown_value: f"List of {markdown_value}"
                )  # noqa: E731

                if "items" not in prop.keys():
                    LOG.warning(
                        'Warning: found schema property of array type with no "items" key; \n'
                        'defaulting data types for array items to "Map" in generated docs.\n'
                        'If "Map" is not what you need instead, specify the expected data type \n'
                        "for array items such as (example with items of string type):\n"
                        '    "ExampleProperty" : {\n'
                        '        "description" : "Example description.",\n'
                        '        "type": "array",\n'
                        '        "items": {\n'
                        '            "type": "string"\n'
                        "        }\n"
                        "    }\n"
                    )
                    prop["items"] = {}

                # potential circular ref
                # setting up markdown before going deep in the heap to reuse markdown
                if "$ref" in prop["items"]:
                    sub_propname = prop["items"]["$ref"][1]  # get target sub property
                    sub_proppath = (sub_propname,)

                    # calculating hypothetical markdown property before
                    # actually traversing it - this way it could be reused -
                    # same .md doc could be attached to both instances
                    hypothetical = markdown_lambda(
                        "-".join(sub_proppath).lower() + ".md", sub_propname
                    )

                    # assigning hypothetical .md file reference before circular
                    # property gets boundary so when boundary is hit it reuses
                    # the same document
                    self._marked_down_properties[propname] = {
                        jsontype: markdown_json(hypothetical),
                        yamltype: markdown_yaml(hypothetical),
                        longformtype: markdown_long(hypothetical),
                    }

                # traverse nested propertes
                prop["arrayitems"] = arrayitems = self._set_docs_properties(
                    propname, prop["items"], proppath
                )

                # arrayitems should be similar to markdown of hypothetical target
                # if there is an object ref
                # arrayitems are could not be used for hypothetical values
                # as could not be populated before traversing down
                type_json = markdown_json(arrayitems[jsontype])
                type_yaml = markdown_yaml(arrayitems[yamltype])
                type_longform = markdown_long(arrayitems[longformtype])

            elif prop_type == "object":
                template = self.env.get_template("docs-subproperty.md")
                docs_path = self.root / "docs"

                object_properties = (
                    prop.get("properties") or prop.get("patternProperties") or {}
                )

                type_json = type_yaml = type_longform = "Map"
                if object_properties:
                    subproperty_name = " ".join(proppath)
                    subproperty_filename = "-".join(proppath).lower() + ".md"
                    subproperty_path = docs_path / subproperty_filename

                    type_json = type_yaml = type_longform = markdown_lambda(
                        subproperty_filename, propname
                    )

                    # potential circular ref
                    # setting up markdown before going deep in the heap
                    # to reuse markdown
                    self._marked_down_properties[propname] = {
                        jsontype: type_json,
                        yamltype: type_json,
                        longformtype: type_json,
                    }

                    prop["properties"] = {
                        name: self._set_docs_properties(name, value, proppath + (name,))
                        for name, value in object_properties.items()
                    }

                    LOG.debug(
                        "Writing docs %s: %s", subproperty_filename, subproperty_path
                    )
                    contents = template.render(
                        type_name=self.type_name,
                        subproperty_name=subproperty_name,
                        schema=prop,
                    )

                    self.safewrite(subproperty_path, contents)

            prop[jsontype] = __join(prop.get(jsontype), type_json)
            prop[yamltype] = __join(prop.get(yamltype), type_yaml)
            prop[longformtype] = __join(prop.get(longformtype), type_longform)
            if "enum" in prop:
                prop["allowedvalues"] = prop["enum"]

        prop_type = prop.get("type", "object")

        single_item = False
        if not isinstance(prop_type, list):
            prop_type = [prop_type]
            single_item = True

        for prop_item in prop_type:
            if isinstance(prop_item, tuple):  # if tuple, then it's a ref
                # using doc method to generate the mdo and reassign the ref
                resolved = self._set_docs_properties(
                    propname, {"$ref": prop_item}, proppath
                )
                prop[jsontype] = __join(prop.get(jsontype), resolved[jsontype])
                prop[yamltype] = __join(prop.get(yamltype), resolved[yamltype])
                prop[longformtype] = __join(
                    prop.get(longformtype), resolved[longformtype]
                )
            else:
                __set_property_type(prop_item, single_type=single_item)
        return prop

    def _upload(
        self,
        fileobj,
        endpoint_url,
        region_name,
        role_arn,
        use_role,
        set_default,
        profile_name,
    ):  # pylint: disable=too-many-arguments, too-many-locals
        LOG.debug("Packaging complete, uploading...")
        session = create_sdk_session(region_name, profile_name)
        LOG.debug("Uploading to region '%s'", session.region_name)
        cfn_client = session.client("cloudformation", endpoint_url=endpoint_url)
        s3_client = session.client("s3")
        uploader = Uploader(cfn_client, s3_client)

        if use_role and not role_arn and "handlers" in self.schema:
            LOG.debug("Creating execution role for provider to use")
            if self.artifact_type == ARTIFACT_TYPE_HOOK:
                role_template_file = HOOK_ROLE_TEMPLATE_FILENAME
            else:
                role_template_file = RESOURCE_ROLE_TEMPLATE_FILENAME
            role_arn = uploader.create_or_update_role(
                self.root / role_template_file, self.hypenated_name
            )

        s3_url = uploader.upload(self.hypenated_name, fileobj)
        LOG.debug("Got S3 URL: %s", s3_url)
        log_delivery_role = uploader.get_log_delivery_role_arn()
        LOG.debug("Got Log Role: %s", log_delivery_role)
        kwargs = {
            "Type": self.artifact_type,
            "TypeName": self.type_name,
            "SchemaHandlerPackage": s3_url,
            "ClientRequestToken": str(uuid4()),
            "LoggingConfig": {
                "LogRoleArn": log_delivery_role,
                "LogGroupName": f"{self.hypenated_name}-logs",
            },
        }
        if role_arn and use_role:
            kwargs["ExecutionRoleArn"] = role_arn

        try:
            response = cfn_client.register_type(**kwargs)

        except ClientError as e:
            LOG.debug("Registering type resulted in unknown ClientError", exc_info=e)
            raise DownstreamError("Unknown CloudFormation error") from e

        self._wait_for_registration(
            cfn_client, response["RegistrationToken"], set_default
        )

    @staticmethod
    def _wait_for_registration(cfn_client, registration_token, set_default):
        registration_waiter = cfn_client.get_waiter("type_registration_complete")
        try:
            LOG.warning(
                "Successfully submitted type. "
                "Waiting for registration with token '%s' to complete.",
                registration_token,
            )
            registration_waiter.wait(RegistrationToken=registration_token)
        except WaiterError as e:
            LOG.warning(
                "Failed to register the type with registration token '%s'.",
                registration_token,
            )
            try:
                response = cfn_client.describe_type_registration(
                    RegistrationToken=registration_token
                )
            except ClientError as describe_error:
                LOG.debug(
                    "Describing type registration resulted in unknown ClientError",
                    exc_info=e,
                )
                raise DownstreamError(
                    "Error describing type registration"
                ) from describe_error
            LOG.warning(
                "Please see response for additional information: '%s'", response
            )
            raise DownstreamError("Type registration error") from e
        LOG.warning("Registration complete.")
        response = cfn_client.describe_type_registration(
            RegistrationToken=registration_token
        )
        LOG.warning(response)
        if set_default:
            arn = response["TypeVersionArn"]
            try:
                cfn_client.set_type_default_version(Arn=arn)
            except ClientError as e:
                LOG.debug(
                    "Setting default version resulted in unknown ClientError",
                    exc_info=e,
                )
                raise DownstreamError("Error setting default version") from e
            LOG.warning("Set default version to '%s", arn)

    # flake8: noqa: C901
    # pylint: disable=R0914
    def _load_target_info(
        self,
        endpoint_url,
        region_name,
        type_schemas=None,
        local_only=False,
        profile_name=None,
    ):  # pylint: disable=too-many-arguments
        if self.artifact_type != ARTIFACT_TYPE_HOOK or not self.schema:
            return {}

        if type_schemas is None:
            type_schemas = []

        if os.path.isdir(self.target_schemas_path):
            for filename in os.listdir(self.target_schemas_path):
                absolute_path = self.target_schemas_path / filename
                if absolute_path.is_file() and absolute_path.match("*.json"):
                    type_schemas.append(str(absolute_path))

        local_info = {}
        if os.path.isfile(self.target_info_path):
            try:
                with self.target_info_path.open("r", encoding="utf-8") as f:
                    local_info = json.load(f)
            except json.JSONDecodeError as e:
                self._raise_invalid_project(
                    f"Target info file '{self.target_info_path}' is invalid", e
                )

        target_names = {
            target_name
            for handler in self.schema.get("handlers", {}).values()
            for target_name in handler.get("targetNames", [])
        }

        LOG.debug("Hook schema target names: %s", str(target_names))

        if self.artifact_type == ARTIFACT_TYPE_HOOK:
            target_names = list(
                filter(lambda x: x not in HOOK_SPECIAL_TARGET_NAMES, target_names)
            )

        if local_only:
            targets = TypeNameResolver.resolve_type_names_locally(
                target_names, local_info
            )
            loader = TypeSchemaLoader(None, None, local_only=local_only)
        else:
            session = create_sdk_session(region_name, profile_name)
            cfn_client = session.client("cloudformation", endpoint_url=endpoint_url)
            s3_client = session.client("s3")

            targets = TypeNameResolver(cfn_client).resolve_type_names(target_names)
            loader = TypeSchemaLoader(cfn_client, s3_client, local_only=local_only)

        LOG.debug("Retrieving info for following target names: %s", str(targets))

        type_info = loader.load_type_info(
            targets, local_schemas=type_schemas, local_info=local_info
        )

        missing_targets = {target for target in targets if target not in type_info}

        if missing_targets:
            raise InvalidProjectError(
                f"Type info missing for the following targets: {missing_targets}"
            )

        return type_info

    def generate_canary_files(self, local_code_generation=False) -> None:
        if (
            not self.file_generation_enabled
            or not Path(self.target_contract_test_folder_path).exists()
            or not local_code_generation
        ):
            LOG.info("Skipping Canary Auto-Generation")
            return
        LOG.info("Starting Canary Auto-Generation...")
        self._setup_stack_template_environment()
        self._generate_stack_template_files()
        LOG.info("Finished Canary Auto-Generation")

    def _setup_stack_template_environment(self) -> None:
        stack_template_root = Path(self.target_canary_root_path)
        stack_template_folder = Path(self.target_canary_folder_path)
        stack_template_folder.mkdir(parents=True, exist_ok=True)
        dependencies_file = (
            Path(self.target_contract_test_folder_path)
            / CONTRACT_TEST_DEPENDENCY_FILE_NAME
        )
        bootstrap_file = stack_template_root / CANARY_DEPENDENCY_FILE_NAME
        if dependencies_file.exists():
            LOG.debug("Writing: %s", bootstrap_file)
            shutil.copy(str(dependencies_file), str(bootstrap_file))
        else:
            LOG.debug(
                "Not found: %s. Not writing to: %s", dependencies_file, bootstrap_file
            )

    def _generate_stack_template_files(self) -> None:
        stack_template_folder = Path(self.target_canary_folder_path)
        contract_test_folder = Path(self.target_contract_test_folder_path)
        contract_test_files = [
            file
            for file in contract_test_folder.glob(CONTRACT_TEST_INPUT_PREFIX)
            if file.is_file() and file.name in self.contract_test_file_names
        ]
        contract_test_files = sorted(contract_test_files)
        for count, ct_file in enumerate(contract_test_files, start=1):
            LOG.debug("Loading contract test input file: %s", ct_file)
            with ct_file.open("r") as f:
                json_data = json.load(f)
            resource_name = self.type_info[2]

            self._save_stack_template_data(
                resource_name,
                count,
                stack_template_folder,
                self._replace_dynamic_values(
                    json_data[CREATE_INPUTS_KEY],
                ),
                CANARY_FILE_CREATE_SUFFIX,
            )
            if PATCH_INPUTS_KEY in json_data:
                supported_patch_inputs = self._translate_supported_patch_inputs(
                    json_data[PATCH_INPUTS_KEY]
                )
                patch_data = jsonpatch.apply_patch(
                    json_data[CREATE_INPUTS_KEY], supported_patch_inputs, in_place=False
                )
                self._save_stack_template_data(
                    resource_name,
                    count,
                    stack_template_folder,
                    patch_data,
                    CANARY_FILE_UPDATE_SUFFIX,
                )

    def _save_stack_template_data(
        self,
        resource_name,
        contract_test_input_count,
        stack_template_folder,
        properties_data,
        suffix,
    ):
        stack_template_data = {
            "Description": f"Template for {self.type_name}",
            "Resources": {
                f"{resource_name}": {
                    "Type": self.type_name,
                    "Properties": properties_data,
                }
            },
        }
        stack_template_file_name = (
            f"{CANARY_FILE_PREFIX}{contract_test_input_count}_{suffix}.yaml"
        )
        stack_template_file_path = stack_template_folder / stack_template_file_name
        LOG.debug("Writing Canary Stack Template File: %s", stack_template_file_path)
        with stack_template_file_path.open("w") as stack_template_file:
            yaml.dump(stack_template_data, stack_template_file, indent=2)

    def _translate_supported_patch_inputs(self, patch_inputs: Any) -> Any:
        output = []
        for patch_input in patch_inputs:
            if (
                patch_input.get(PATCH_OPERATION_KEY)
                in CANARY_SUPPORTED_PATCH_INPUT_OPERATIONS
            ):
                if PATCH_VALUE_KEY in patch_input:
                    self._replace_dynamic_values_with_root_key(
                        patch_input, PATCH_VALUE_KEY
                    )
                output.append(patch_input)
        return output

    def _replace_dynamic_values(self, properties: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in properties.items():
            if isinstance(value, dict):
                properties[key] = self._replace_dynamic_values(value)
            elif isinstance(value, list):
                properties[key] = [self._replace_dynamic_value(item) for item in value]
            else:
                return_value = self._replace_dynamic_value(value)
                properties[key] = return_value
        return properties

    def _replace_dynamic_values_with_root_key(
        self, properties: Dict[str, Any], root_key=None
    ) -> Dict[str, Any]:
        value = properties[root_key]
        if isinstance(value, dict):
            properties[root_key] = self._replace_dynamic_values(value)
        elif isinstance(value, list):
            properties[root_key] = [self._replace_dynamic_value(item) for item in value]
        else:
            return_value = self._replace_dynamic_value(value)
            properties[root_key] = return_value
        return properties

    def _replace_dynamic_value(self, original_value: Any) -> Any:
        pattern = r"\{\{(.*?)\}\}"

        def replace_token(match):
            token = match.group(1)
            if UUID in token:
                return str(uuid4())
            if token in DYNAMIC_VALUES_MAP:
                return DYNAMIC_VALUES_MAP[token]
            return f'{{"{FN_IMPORT_VALUE}": "{token.strip()}"}}'

        replaced_value = re.sub(pattern, replace_token, str(original_value))

        if any(value in replaced_value for value in DYNAMIC_VALUES_MAP.values()):
            replaced_value = {FN_SUB: replaced_value}
        if FN_IMPORT_VALUE in replaced_value:
            replaced_value = json.loads(replaced_value)
        return replaced_value
