import json
import logging
import os
import re
import shutil
from io import TextIOWrapper
from pathlib import Path

import pkg_resources
import yaml
from jsonschema import Draft7Validator, RefResolver
from jsonschema.exceptions import RefResolutionError, ValidationError
from nested_lookup import nested_lookup

from .exceptions import InternalError, SpecValidationError
from .jsonutils.flattener import JsonSchemaFlattener
from .jsonutils.inliner import RefInliner
from .jsonutils.utils import FlatteningError

LOG = logging.getLogger(__name__)

TIMEOUT_IN_SECONDS = 10
STDIN_NAME = "<stdin>"
MAX_CONFIGURATION_SCHEMA_LENGTH = 60 * 1024  # 60 KiB


def resource_stream(package_name, resource_name, encoding="utf-8"):
    """Load a package resource as a decoded file-like object.

    By default, package resources are loaded as binary files, which isn't a
    use-case for us.

    Decoding errors raise :exc:`ValueError`. :term:`universal newlines`
    are enabled. Can be used in a ``with`` statement.
    """
    f = pkg_resources.resource_stream(package_name, resource_name)
    return TextIOWrapper(f, encoding=encoding)


def resource_json(package_name, resource_name):
    """Load a JSON package resource and return the parsed object."""
    with resource_stream(package_name, resource_name) as f:
        return json.load(f)


def resource_yaml(package_name, resource_name):
    """Load a YAML package resource and return the parsed object."""
    with resource_stream(package_name, resource_name) as f:
        return yaml.safe_load(f)


def copy_resource(package_name, resource_name, out_path):
    with pkg_resources.resource_stream(
        package_name, resource_name
    ) as fsrc, out_path.open("wb") as fdst:
        shutil.copyfileobj(fsrc, fdst)


def get_schema_store(schema_search_path):
    """Load all the schemas in schema_search_path and return a dict"""
    schema_store = {}
    schema_fnames = os.listdir(schema_search_path)
    for schema_fname in schema_fnames:
        schema_path = os.path.join(schema_search_path, schema_fname)
        if schema_path.endswith(".json"):
            with open(schema_path, "r", encoding="utf-8") as schema_f:
                schema = json.load(schema_f)
                if "$id" in schema:
                    schema_store[schema["$id"]] = schema
    return schema_store


def make_validator(schema):
    schema_search_path = Path(os.path.dirname(os.path.realpath(__file__))).joinpath(
        "data/schema/"
    )
    resolver = RefResolver(
        base_uri=Draft7Validator.ID_OF(schema),
        store=get_schema_store(schema_search_path),
        referrer=schema,
    )
    return Draft7Validator(schema, resolver=resolver)


def make_resource_validator():
    schema = resource_json(__name__, "data/schema/provider.definition.schema.v1.json")
    return make_validator(schema)


def make_resource_validator_with_additional_properties_check():
    schema = resource_json(__name__, "data/schema/base.definition.schema.v1.json")
    dependencies = schema["definitions"]["validations"]["dependencies"]
    properties_check = {
        "properties": {
            "$comment": (
                "An object cannot have both defined and undefined properties;"
                " therefore, patternProperties is not allowed when properties is"
                " specified. Provider should mark additionalProperties as false if the"
                " property is of object type and has properties defined in it."
            ),
            "not": {"required": ["patternProperties"]},
            "required": ["additionalProperties"],
        }
    }
    pattern_properties_check = {
        "patternProperties": {
            "$comment": (
                "An object cannot have both defined and undefined properties;"
                " therefore, properties is not allowed when patternProperties is"
                " specified. Provider should mark additionalProperties as false if the"
                " property is of object type and has patternProperties defined in it."
            ),
            "not": {"required": ["properties"]},
            "required": ["additionalProperties"],
        }
    }
    schema["definitions"]["validations"]["dependencies"] = {
        **dependencies,
        **properties_check,
        **pattern_properties_check,
    }
    return make_validator(schema)


def make_hook_validator():
    schema = resource_json(
        __name__, "data/schema/provider.definition.schema.hooks.v1.json"
    )
    return make_validator(schema)


def get_file_base_uri(file):
    try:
        name = file.name
    except AttributeError:
        LOG.error(
            "Resource spec has no filename associated, relative references may not work"
        )
        name = STDIN_NAME

    if name == STDIN_NAME:
        path = Path.cwd() / "-"  # fake file
    else:
        path = Path(name)
    return path.resolve().as_uri()


def load_resource_spec(resource_spec_file):  # pylint: disable=R # noqa: C901
    """Load a resource provider definition from a file, and validate it."""
    try:
        resource_spec = json.load(resource_spec_file)
    except ValueError as e:
        LOG.debug("Resource spec decode failed", exc_info=True)
        raise SpecValidationError(str(e)) from e

    # check TypeConfiguration schema size
    if len(json.dumps(resource_spec).encode("utf-8")) > MAX_CONFIGURATION_SCHEMA_LENGTH:
        raise SpecValidationError(
            "TypeConfiguration schema exceeds maximum length of 60 KiB"
        )

    validator = make_resource_validator()
    additional_properties_validator = (
        make_resource_validator_with_additional_properties_check()
    )
    try:
        validator.validate(resource_spec)
    except ValidationError as e:
        LOG.debug("Resource spec validation failed", exc_info=True)
        raise SpecValidationError(str(e)) from e

    try:  # pylint: disable=R
        for _key, schema in JsonSchemaFlattener(resource_spec).flatten_schema().items():
            for property_name, property_details in schema.get("properties", {}).items():
                if property_name[0].islower():
                    LOG.warning(
                        "CloudFormation properties don't usually start with lowercase"
                        " letters: %s",
                        property_name,
                    )
                try:
                    property_type = property_details["type"]
                    property_keywords = property_details.keys()
                    if (
                        property_type == "array"
                        and "insertionOrder" not in property_keywords
                    ):
                        LOG.warning(
                            "Explicitly specify value for insertionOrder for array: %s",
                            property_name,
                        )
                    if property_type != "array" and "arrayType" in property_keywords:
                        raise SpecValidationError(
                            "arrayType is only applicable for properties of type array"
                        )
                    keyword_mappings = [
                        (
                            {"integer", "number"},
                            {
                                "minimum",
                                "maximum",
                                "exclusiveMinimum",
                                "exclusiveMaximum",
                                "multipleOf",
                            },
                        ),
                        (
                            {"string"},
                            {
                                "minLength",
                                "maxLength",
                                "pattern",
                            },
                        ),
                        (
                            {"object"},
                            {
                                "minProperties",
                                "maxProperties",
                                "additionalProperties",
                                "patternProperties",
                            },
                        ),
                        (
                            {"array"},
                            {
                                "minItems",
                                "maxItems",
                                "additionalItems",
                                "uniqueItems",
                            },
                        ),
                    ]
                    type_specific_keywords = set().union(
                        *(mapping[1] for mapping in keyword_mappings)
                    )
                    for types, allowed_keywords in keyword_mappings:
                        if (
                            property_type in types
                            and type_specific_keywords - allowed_keywords
                            & property_keywords
                        ):
                            LOG.warning(
                                "Incorrect JSON schema keyword(s) %s for type: %s for"
                                " property: %s",
                                type_specific_keywords - allowed_keywords
                                & property_keywords,
                                property_type,
                                property_name,
                            )
                except (KeyError, TypeError):
                    pass
    except FlatteningError:
        pass

    for pattern in nested_lookup("pattern", resource_spec):
        if "arn:aws:" in pattern:
            LOG.warning(
                "Don't hardcode the aws partition in ARN patterns: %s",
                pattern,
            )
        try:
            # http://json-schema.org/understanding-json-schema/reference/regular_expressions.html
            # ECMA-262 has \w, \W, \b, \B, \d, \D, \s and \S perform ASCII-only matching
            # instead of full Unicode matching. Unicode matching is the default in Python
            re.compile(pattern, re.ASCII)
        except re.error:
            LOG.warning("Could not validate regular expression: %s", pattern)

    for enum in nested_lookup("enum", resource_spec):
        if len(enum) > 15:
            LOG.warning(
                "Consider not manually maintaining large constantly evolving enums like"
                " instance types, lambda runtimes, partitions, regions, availability"
                " zones, etc. that get outdated quickly: %s",
                enum,
            )

    non_ascii_chars = re.findall(
        r"[^ -~]", json.dumps(resource_spec, ensure_ascii=False)
    )
    if non_ascii_chars:
        LOG.warning(
            "non-ASCII characters found in resource schema: %s", non_ascii_chars
        )

    list_options = {
        "maxresults",
        "maxrecords",
        "maxitems",
        "nexttoken",
        "nextmarker",
        "nextpagetoken",
        "pagetoken",
        "paginationtoken",
    } & set(map(str.lower, resource_spec.get("properties", [])))
    if list_options:
        LOG.warning(
            "LIST API inputs like MaxResults, MaxRecords, MaxItems, NextToken,"
            " NextMarker, NextPageToken, PageToken, and Filters are not resource"
            " properties. %s should not be present in resource schema",
            list_options,
        )

    read_only_properties = set(resource_spec.get("readOnlyProperties", []))
    create_only_properties = set(resource_spec.get("createOnlyProperties", []))
    conditional_create_only_properties = set(
        resource_spec.get("conditionalCreateOnlyProperties", [])
    )

    read_only_properties_intersection = read_only_properties & (
        create_only_properties
        | set(resource_spec.get("writeOnlyProperties", []))
        | {"/properties/" + s for s in resource_spec.get("required", [])}
    )
    if read_only_properties_intersection:
        LOG.warning(
            "readOnlyProperties cannot be specified by customers and should not overlap"
            " with writeOnlyProperties, createOnlyProperties, or required: %s",
            read_only_properties_intersection,
        )

    verify_listed_properties_are_defined(resource_spec)

    for handler in resource_spec.get("handlers", []):
        for permission in resource_spec.get("handlers", [])[handler]["permissions"]:
            if "*" in permission:
                LOG.warning(
                    "Use specific handler permissions instead of using wildcards: %s",
                    permission,
                )

    try:
        additional_properties_validator.validate(resource_spec)
    except ValidationError as e:
        LOG.warning(
            "[Warning] Resource spec validation would fail from next major version."
            " Provider should mark additionalProperties as false if the property is of"
            " object type and has properties or patternProperties defined in it. Please"
            " fix the warnings: %s",
            str(e),
        )

    for primary_id in resource_spec["primaryIdentifier"]:
        if (
            primary_id not in read_only_properties
            and primary_id not in create_only_properties
        ):
            LOG.warning(
                "Property 'primaryIdentifier' - %s must be specified as either readOnly"
                " or createOnly",
                primary_id,
            )

    if conditional_create_only_properties & create_only_properties:
        raise SpecValidationError(
            "createOnlyProperties and conditionalCreateOnlyProperties MUST NOT have"
            " common properties"
        )

    if conditional_create_only_properties & read_only_properties:
        raise SpecValidationError(
            "readOnlyProperties and conditionalCreateOnlyProperties MUST NOT have"
            " common properties"
        )

    if "tagging" not in resource_spec:
        LOG.warning("Explicitly specify value for tagging")

    # TODO: more general validation framework
    if "remote" in resource_spec:
        raise SpecValidationError(
            "Property 'remote' is reserved for CloudFormation use"
        )

    try:
        base_uri = resource_spec["$id"]
    except KeyError:
        base_uri = get_file_base_uri(resource_spec_file)

    inliner = RefInliner(base_uri, resource_spec)
    try:
        inlined = inliner.inline()
    except RefResolutionError as e:
        LOG.debug("Resource spec validation failed", exc_info=True)
        raise SpecValidationError(str(e)) from e

    try:
        validator.validate(inlined)
    except ValidationError as e:
        LOG.debug("Inlined schema is no longer valid", exc_info=True)
        raise InternalError() from e

    return inlined

def verify_listed_properties_are_defined(resource_spec):
    LOG.debug("verify_listed_properties_are_defined")

    schema_keys = [
        "readOnlyProperties",
        "createOnlyProperties",
        "primaryIdentifier",
        "required",
        "additionalIdentifiers",
        "deprecatedProperties",
        "writeOnlyProperties",
    ]
    for schema_key in schema_keys:
        verify_top_level_properties_are_defined(resource_spec, schema_key)

def verify_top_level_properties_are_defined(resource_spec, schema_key):
    LOG.debug("verify_top_level_properties_are_defined: %s", schema_key)

    properties = get_properties_from_schema_key(resource_spec, schema_key)

    nested_properties = set(filter(lambda property: "/" in property, properties))
    top_level_properties = properties - nested_properties

    if len(top_level_properties):
        defined_properties = get_defined_properties(resource_spec)
        nondefined_top_level_properties = top_level_properties - defined_properties

        if nondefined_top_level_properties:
            raise SpecValidationError(
                get_properties_not_defined_error_message(schema_key, nondefined_top_level_properties)
            )

def get_defined_properties(resource_spec):
    return set(resource_spec.get("properties", []))

def get_properties_from_schema_key(resource_spec, schema_key):
    if schema_key == "additionalIdentifiers":
        # additionalIdentifiers is a 2 dimensional list, we need to flatten it
        properties = resource_spec.get(schema_key, [[]])
        properties = {prop for identifier in properties for prop in identifier}
    else:
        properties = resource_spec.get(schema_key, [])

    return set(map(lambda property: property.replace("/properties/", ""), properties))

def get_properties_not_defined_error_message(key, properties):
    return f"The following properties are listed in '{key}' but not defined in 'properties': {', '.join(properties)}"


def load_hook_spec(hook_spec_file):  # pylint: disable=R # noqa: C901
    """Load a hook definition from a file, and validate it."""
    try:
        hook_spec = json.load(hook_spec_file)
    except ValueError as e:
        LOG.debug("Hook spec decode failed", exc_info=True)
        raise SpecValidationError(str(e)) from e

    # TODO: Add schema validation after we have hook schema finalized

    if hook_spec.get("properties"):
        raise SpecValidationError(
            "Hook types do not support 'properties' directly. Properties must be"
            " specified in the 'typeConfiguration' section."
        )

    validator = make_hook_validator()
    try:
        validator.validate(hook_spec)
    except ValidationError as e:
        LOG.debug("Hook spec validation failed", exc_info=True)
        raise SpecValidationError(str(e)) from e

    blocked_handler_permissions = {"cloudformation:RegisterType"}
    for handler in hook_spec.get("handlers", {}).values():
        for permission in handler["permissions"]:
            if "cloudformation:*" in permission:
                raise SpecValidationError(
                    "Wildcards for cloudformation are not allowed for hook handler"
                    f" permissions: '{permission}'"
                )
            if permission in blocked_handler_permissions:
                raise SpecValidationError(
                    "Permission is not allowed for hook handler permissions:"
                    f" '{permission}'"
                )

        for target_name in handler["targetNames"]:
            if "*?" in target_name:
                raise SpecValidationError(
                    "Wildcard pattern '*?' is not allowed in target name:"
                    f" '{target_name}'"
                )

    try:
        base_uri = hook_spec["$id"]
    except KeyError:
        base_uri = get_file_base_uri(hook_spec_file)

    inliner = RefInliner(base_uri, hook_spec)
    try:
        inlined = inliner.inline()
    except RefResolutionError as e:
        LOG.debug("Hook spec validation failed", exc_info=True)
        raise SpecValidationError(str(e)) from e

    return inlined
