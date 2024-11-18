# fixture and parameter have the same name
# pylint: disable=redefined-outer-name
# pylint: disable=import-outside-toplevel
import json
from contextlib import contextmanager
from io import BytesIO, StringIO
from pathlib import Path
from subprocess import check_output
from unittest.mock import ANY, create_autospec, patch

import pytest
import yaml
from jsonschema.exceptions import RefResolutionError, ValidationError
from pytest_localserver.http import Request, Response, WSGIServer

from rpdk.core.data_loaders import (
    STDIN_NAME,
    get_file_base_uri,
    get_schema_store,
    load_hook_spec,
    load_resource_spec,
    resource_json,
    resource_stream,
    resource_yaml,
)
from rpdk.core.exceptions import InternalError, SpecValidationError
from rpdk.core.plugin_base import LanguagePlugin

BASEDIR = Path(__file__).parent  # tests/test_data_loaders.py -> tests/

# Lonely continuation byte is invalid
# https://www.cl.cam.ac.uk/~mgk25/ucs/examples/UTF-8-test.txt
INVALID_UTF8 = b"\x80"

BASIC_SCHEMA = {
    "typeName": "AWS::FOO::BAR",
    "description": "test schema",
    "properties": {"foo": {"type": "string"}},
    "primaryIdentifier": ["/properties/foo"],
    "readOnlyProperties": ["/properties/foo"],
    "additionalProperties": False,
}

HOOK_BASIC_SCHEMA = {
    "typeName": "AWS::FOO::BAR",
    "description": "test schema",
    "typeConfiguration": {
        "properties": {"foo": {"type": "string"}},
        "additionalProperties": False,
    },
    "additionalProperties": False,
}


def json_s(obj):
    return StringIO(json.dumps(obj))


@contextmanager
def wsgi_serve(application):
    server = WSGIServer(application=application)
    try:
        server.start()
        yield server
    finally:
        server.stop()


def test_load_resource_spec_invalid_json():
    with pytest.raises(SpecValidationError) as excinfo:
        load_resource_spec(StringIO('{"foo": "aaaaa}'))

    assert "line 1" in str(excinfo.value)
    assert "column 9" in str(excinfo.value)


def test_load_resource_spec_empty_is_invalid():
    with pytest.raises(SpecValidationError):
        load_resource_spec(StringIO(""))


def test_load_resource_spec_boolean_is_invalid():
    with pytest.raises(SpecValidationError):
        load_resource_spec(json_s(True))


def test_load_resource_spec_empty_object_is_invalid():
    with pytest.raises(SpecValidationError):
        load_resource_spec(json_s({}))


def test_load_hook_spec_invalid_json():
    with pytest.raises(SpecValidationError) as excinfo:
        load_hook_spec(StringIO('{"foo": "aaaaa}'))

    assert "line 1" in str(excinfo.value)
    assert "column 9" in str(excinfo.value)


def json_files_params(path, glob="*.json"):
    return tuple(pytest.param(p, id=p.name) for p in path.glob(glob))


@pytest.mark.parametrize(
    "example", json_files_params(BASEDIR / "data" / "schema" / "valid")
)
def test_load_resource_spec_valid_snippets(example):
    with example.open("r", encoding="utf-8") as f:
        assert load_resource_spec(f)


@pytest.mark.parametrize(
    "schema",
    [
        "valid_nested_property_object_no_additionalProperties_warning.json",
        "valid_pattern_properties_no_additionalProperties_warning.json",
    ],
)
def test_load_resource_spec_object_property_missing_additional_properties(
    schema, caplog
):
    schema = BASEDIR / "data" / "schema" / "valid" / schema
    with schema.open("r", encoding="utf-8") as f:
        assert load_resource_spec(f)
    assert "Resource spec validation would fail from next major version" in caplog.text


def test_load_resource_spec_unmodeled_object_property_missing_additional_properties(
    caplog,
):
    schema = BASEDIR / "data" / "schema" / "valid" / "valid_no_properties.json"
    with schema.open("r", encoding="utf-8") as f:
        assert load_resource_spec(f)
    assert (
        "Resource spec validation would fail from next major version" not in caplog.text
    )


def test_load_resource_spec_conditionally_create_only_match_create_only():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "additionalProperties": False,
        "properties": {"foo": {"type": "string"}, "bar": {"type": "string"}},
        "primaryIdentifier": ["/properties/foo"],
        "readOnlyProperties": ["/properties/foo"],
        "createOnlyProperties": ["/properties/bar"],
        "conditionalCreateOnlyProperties": ["/properties/bar"],
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_resource_spec(json_s(schema))
    assert (
        str(excinfo.value)
        == "createOnlyProperties and conditionalCreateOnlyProperties MUST NOT have"
        " common properties"
    )


def test_load_resource_spec_conditionally_create_only_match_read_only():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "additionalProperties": False,
        "properties": {"foo": {"type": "string"}},
        "primaryIdentifier": ["/properties/foo"],
        "readOnlyProperties": ["/properties/foo"],
        "conditionalCreateOnlyProperties": ["/properties/foo"],
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_resource_spec(json_s(schema))
    assert (
        str(excinfo.value)
        == "readOnlyProperties and conditionalCreateOnlyProperties MUST NOT have common"
        " properties"
    )


def test_load_resource_spec_non_public_properties_and_definitions():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "additionalProperties": False,
        "definitions": {"bar": {"type": "string"}},
        "properties": {"foo": {"type": "string"}},
        "primaryIdentifier": ["/properties/foo"],
        "readOnlyProperties": ["/properties/foo"],
        "nonPublicProperties": ["/properties/foo"],
        "nonPublicDefinitions": ["/definitions/bar"],
    }
    load_resource_spec(json_s(schema))


@pytest.mark.parametrize(
    "schema",
    [
        "invalid_nested_property_object_additionalProperties_true_warning.json",
        "invalid_pattern_properties_additionalProperties_true_warning.json",
    ],
)
def test_load_resource_spec_object_property_additional_properties_true(schema):
    schema = BASEDIR / "data" / "schema" / "invalid" / schema
    with schema.open("r", encoding="utf-8") as f:
        with pytest.raises(SpecValidationError) as excinfo:
            load_resource_spec(f)
    assert "False was expected" in str(excinfo.value)


@pytest.mark.parametrize(
    "example", json_files_params(BASEDIR / "data" / "schema" / "invalid")
)
def test_load_resource_spec_invalid_snippets(example):
    with example.open("r", encoding="utf-8") as f:
        with pytest.raises(SpecValidationError):
            load_resource_spec(f)

@pytest.mark.parametrize(
    "test_props",
    [
        {
            'schema': "invalid_read_only_properties_not_defined.json",
            'schema_key': "readOnlyProperties",
            'property': "properties/property2",
        },
        {
            'schema': "invalid_create_only_properties_not_defined.json",
            'schema_key': "createOnlyProperties",
            'property': "properties/property2",
        },
        {
            'schema': "invalid_primary_identifier_not_defined.json",
            'schema_key': "primaryIdentifier",
            'property': "properties/property2",
        },
        {
            'schema': "invalid_required_not_defined.json",
            'schema_key': "required",
            'property': "property2",
        },
        {
            'schema': "invalid_additional_identifiers_not_defined.json",
            'schema_key': "additionalIdentifiers",
            'property': "/properties/property2",
        },
        {
            'schema': "invalid_deprecated_properties_not_defined.json",
            'schema_key': "deprecatedProperties",
            'property': "/properties/property2",
        },
        {
            'schema': "invalid_write_only_properties_not_defined.json",
            'schema_key': "writeOnlyProperties",
            'property': "/properties/property2",
        },
    ],
)
def test_load_resource_spec_property_not_defined(test_props):
    schema = BASEDIR / "data" / "schema" / "invalid" / test_props["schema"]
    with schema.open("r", encoding="utf-8") as f:
        with pytest.raises(SpecValidationError) as excinfo:
            load_resource_spec(f)
            assert f"The following properties are listed in '{test_props['schema_key']}' but not defined in '{test_props['property']}': /properties/property2"in str(excinfo.value)

def test_load_resource_spec_remote_key_is_invalid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "properties": {"foo": {"type": "string"}},
        "primaryIdentifier": ["/properties/foo"],
        "readOnlyProperties": ["/properties/foo"],
        "remote": {},
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_resource_spec(json_s(schema))
    assert "remote" in str(excinfo.value)


def test_load_hook_spec_invalid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "typeConfiguration": {
            "properties": {"foo": {"type": "string"}},
            "additionalProperties": False,
        },
        "handlers": {
            "midCreate": {"targetNames": ["AWS::S3::Bucket"], "permissions": []}
        },
        "additionalProperties": False,
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_hook_spec(json_s(schema))
    assert "Additional properties are not allowed" in str(excinfo.value)
    assert "midCreate" in str(excinfo.value)


@pytest.mark.parametrize(
    "permission", ("cloudformation:RegisterType", "cloudformation:*")
)
def test_load_hook_spec_hook_permissions_invalid(permission):
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "typeConfiguration": {
            "properties": {"foo": {"type": "string"}},
            "additionalProperties": False,
        },
        "handlers": {
            "preCreate": {"targetNames": ["AWS::BAZ::ZAZ"], "permissions": [permission]}
        },
        "additionalProperties": False,
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_hook_spec(json_s(schema))
    assert "not allowed for hook handler permissions" in str(excinfo.value)


def test_load_hook_spec_hook_permissions_valid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "typeConfiguration": {
            "properties": {"foo": {"type": "string"}},
            "additionalProperties": False,
        },
        "handlers": {
            "preDelete": {
                "targetNames": ["AWS::S3::Bucket"],
                "permissions": ["s3:GetObject"],
            }
        },
        "additionalProperties": False,
    }
    result = load_hook_spec(json_s(schema))
    assert result == schema


def test_load_resource_spec_array_type_invalid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "additionalProperties": False,
        "properties": {
            "Foo": {
                "type": "array",
                "uniqueItems": False,
                "insertionOrder": False,
                "items": {"$ref": "#/definitions/XYZ"},
            },
            "Bar": {"type": "string", "arrayType": "Standard"},
        },
        "definitions": {
            "XYZ": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"Value": {"type": "string"}, "Key": {"type": "string"}},
            }
        },
        "primaryIdentifier": ["/properties/Foo"],
        "readOnlyProperties": ["/properties/Foo"],
        "createOnlyProperties": ["/properties/Foo"],
        "conditionalCreateOnlyProperties": ["/properties/Bar"],
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_resource_spec(json_s(schema))
    assert (
        str(excinfo.value)
        == "arrayType is only applicable for properties of type array"
    )


def test_load_resource_spec_array_type_valid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "additionalProperties": False,
        "properties": {
            "Foo": {
                "type": "array",
                "uniqueItems": False,
                "insertionOrder": False,
                "arrayType": "Standard",
                "items": {"$ref": "#/definitions/XYZ"},
            },
            "Bar": {"type": "string"},
        },
        "definitions": {
            "XYZ": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"Value": {"type": "string"}, "Key": {"type": "string"}},
            }
        },
        "primaryIdentifier": ["/properties/Foo"],
        "readOnlyProperties": ["/properties/Foo"],
        "createOnlyProperties": ["/properties/Foo"],
        "conditionalCreateOnlyProperties": ["/properties/Bar"],
    }
    result = load_resource_spec(json_s(schema))
    assert result == schema


def test_load_resource_spec_without_array_type_valid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "additionalProperties": False,
        "properties": {
            "foo": {
                "type": "array",
                "uniqueItems": False,
                "insertionOrder": False,
                "items": {"$ref": "#/definitions/XYZ"},
            },
            "bar": {"type": "string"},
        },
        "definitions": {
            "XYZ": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"Value": {"type": "string"}, "Key": {"type": "string"}},
            }
        },
        "primaryIdentifier": ["/properties/foo"],
        "readOnlyProperties": ["/properties/foo"],
        "createOnlyProperties": ["/properties/foo"],
        "conditionalCreateOnlyProperties": ["/properties/bar"],
    }
    result = load_resource_spec(json_s(schema))
    assert result == schema


def test_load_resource_spec_with_relationship_valid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "additionalProperties": False,
        "properties": {
            "foo": {
                "type": "string",
                "relationshipRef": {
                    "typeName": "ABC::DEF::GHI",
                    "propertyPath": "/properties/id",
                },
            },
            "bar": {"type": "string"},
        },
        "definitions": {
            "XYZ": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"Value": {"type": "string"}, "Key": {"type": "string"}},
            }
        },
        "primaryIdentifier": ["/properties/foo"],
        "readOnlyProperties": ["/properties/foo"],
        "createOnlyProperties": ["/properties/foo"],
        "conditionalCreateOnlyProperties": ["/properties/bar"],
    }
    result = load_resource_spec(json_s(schema))
    assert result == schema


def test_load_resource_spec_with_relationship_invalid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "additionalProperties": False,
        "properties": {
            "foo": {"type": "object", "relationshipRef": {"typeName": "ABC::DEF::GHI"}},
            "bar": {"type": "string"},
        },
        "definitions": {
            "XYZ": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"Value": {"type": "string"}, "Key": {"type": "string"}},
            }
        },
        "primaryIdentifier": ["/properties/foo"],
        "readOnlyProperties": ["/properties/foo"],
        "createOnlyProperties": ["/properties/foo"],
        "conditionalCreateOnlyProperties": ["/properties/bar"],
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_resource_spec(json_s(schema))

    assert "Failed validating" in str(excinfo.value)


def test_load_resource_spec_with_relationship_invalid_pattern():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "additionalProperties": False,
        "properties": {
            "foo": {
                "type": "string",
                "relationshipRef": {
                    "typeName": "string",
                    "propertyPath": "string",
                },
            },
            "bar": {"type": "string"},
        },
        "primaryIdentifier": ["/properties/foo"],
        "readOnlyProperties": ["/properties/foo"],
        "createOnlyProperties": ["/properties/foo"],
        "conditionalCreateOnlyProperties": ["/properties/bar"],
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_resource_spec(json_s(schema))

    assert "does not match '^[a-zA-Z0-9]{2,64}::[a-zA-Z0-9]{2,64}" in str(excinfo.value)


def test_load_hook_spec_properties_key_is_invalid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "typeConfiguration": {
            "properties": {},
            "additionalProperties": False,
        },
        "properties": {"foo": {"type": "string"}},
        "additionalProperties": False,
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_hook_spec(json_s(schema))
    assert "properties" in str(excinfo.value)


def test_load_hook_spec_hook_target_name_pattern_invalid():
    schema = {
        "typeName": "AWS::FOO::BAR",
        "description": "test schema",
        "typeConfiguration": {
            "properties": {"foo": {"type": "string"}},
            "additionalProperties": False,
        },
        "handlers": {
            "preCreate": {"targetNames": ["AWS::S3::Bu*?ket"], "permissions": []}
        },
        "additionalProperties": False,
    }
    with pytest.raises(SpecValidationError) as excinfo:
        load_hook_spec(json_s(schema))
    assert "Wildcard pattern '*?' is not allowed in target name" in str(excinfo.value)


def test_argparse_stdin_name():
    """By default, pytest messes with stdin and stdout, which prevents me from
    writing a test to check we have the right magic name that argparse uses
    for stdin. So I invoke a separate, pristine python process to check.
    """
    code = "; ".join(
        """import argparse
parser = argparse.ArgumentParser()
parser.add_argument("file", type=argparse.FileType("r"))
args = parser.parse_args(["-"])
print(args.file.name)
""".splitlines()
    )

    raw = check_output(["python3", "-c", code])
    result = raw.rstrip().decode("utf-8")  # remove trailing newline
    assert result == STDIN_NAME


def test_get_file_base_uri_file_object_no_name():
    f = json_s(BASIC_SCHEMA)
    assert not hasattr(f, "name")
    expected = (Path.cwd() / "-").resolve().as_uri()
    actual = get_file_base_uri(f)
    assert actual == expected


def test_load_resource_spec_file_object_stdin():
    f = json_s(BASIC_SCHEMA)
    f.name = STDIN_NAME
    expected = (Path.cwd() / "-").resolve().as_uri()
    actual = get_file_base_uri(f)
    assert actual == expected


def test_load_resource_spec_file_object_has_name(tmpdir):
    f = json_s(BASIC_SCHEMA)
    f.name = tmpdir.join("test.json")
    expected = Path(f.name).resolve().as_uri()
    actual = get_file_base_uri(f)
    assert actual == expected


@pytest.mark.parametrize(
    "ref_fn",
    (
        lambda server: server.url + "/bar",  # absolute
        lambda _server: "./bar",  # relative
    ),
)
def test_load_resource_spec_uses_id_if_id_is_set(ref_fn):
    @Request.application
    def application(_request):
        return Response(json.dumps({"type": "string"}), mimetype="application/json")

    with wsgi_serve(application) as server:
        schema = {
            **BASIC_SCHEMA,
            "$id": server.url + "/foo",
            "properties": {"foo": {"$ref": ref_fn(server)}},
        }
        inlined = load_resource_spec(json_s(schema))

    assert inlined["remote"]["schema0"]["type"] == "string"


def test_load_resource_spec_inliner_produced_invalid_schema():
    with patch("rpdk.core.data_loaders.RefInliner", autospec=True) as mock_inliner:
        mock_inliner.return_value.inline.return_value = {}
        with pytest.raises(InternalError) as excinfo:
            load_resource_spec(json_s(BASIC_SCHEMA))

    mock_inliner.assert_called_once_with(ANY, BASIC_SCHEMA)
    cause = excinfo.value.__cause__
    assert cause
    assert isinstance(cause, ValidationError)


def test_load_resource_spec_invalid_ref():
    copy = json.loads(json.dumps(BASIC_SCHEMA))
    copy["properties"]["foo"] = {"$ref": "#/bar"}
    with pytest.raises(SpecValidationError) as excinfo:
        load_resource_spec(json_s(copy))

    cause = excinfo.value.__cause__
    assert cause
    assert isinstance(cause, RefResolutionError)
    assert "bar" in str(cause)


def test_load_hook_spec_invalid_ref():
    copy = json.loads(json.dumps(HOOK_BASIC_SCHEMA))
    copy["typeConfiguration"]["properties"]["foo"] = {"$ref": "#/bar"}
    with pytest.raises(SpecValidationError) as excinfo:
        load_hook_spec(json_s(copy))

    cause = excinfo.value.__cause__
    assert cause
    assert isinstance(cause, RefResolutionError)
    assert "bar" in str(cause)


@pytest.fixture
def plugin():
    mock_plugin = create_autospec(LanguagePlugin)
    mock_plugin.project_settings_defaults.return_value = resource_stream(
        __name__, "data/project_defaults.yaml"
    )
    mock_plugin.project_settings_schema.return_value = resource_json(
        __name__, "data/project_schema.json"
    )
    return mock_plugin


def mock_pkg_resource_stream(bytes_in, func=resource_stream):
    resource_name = "data/test.utf-8"
    target = "rpdk.core.data_loaders.pkg_resources.resource_stream"
    with patch(target, autospec=True, return_value=BytesIO(bytes_in)) as mock_stream:
        f = func(__name__, resource_name)
    mock_stream.assert_called_once_with(__name__, resource_name)
    return f


def test_resource_stream_decoding_valid():
    emoji_santa = "🎅"
    f = mock_pkg_resource_stream(emoji_santa.encode("utf-8"))
    assert f.read() == emoji_santa


def test_resource_stream_decoding_invalid():
    f = mock_pkg_resource_stream(INVALID_UTF8)

    # stream is lazily decoded
    with pytest.raises(UnicodeDecodeError) as excinfo:
        f.read()
    assert excinfo.value.encoding == "utf-8"
    assert excinfo.value.object == INVALID_UTF8


def test_resource_stream_universal_newlines():
    f = mock_pkg_resource_stream(b"Windows\r\n")
    assert f.read() == "Windows\n"


def test_resource_stream_with_statement():
    string = "Hello, World"
    with mock_pkg_resource_stream(string.encode("utf-8")) as f:
        assert f.read() == string

    with pytest.raises(ValueError) as excinfo:
        f.read()
    assert "I/O operation on closed file" in str(excinfo.value)


def test_resource_json():
    obj = {"foo": "bar"}
    encoded = json.dumps(obj).encode("utf-8")
    result = mock_pkg_resource_stream(encoded, func=resource_json)
    assert result == obj


def test_resource_yaml():
    obj = {"foo": "bar"}
    encoded = yaml.dump(obj).encode("utf-8")
    result = mock_pkg_resource_stream(encoded, func=resource_yaml)
    assert result == obj


def test_get_schema_store_schemas_with_id():
    schema_store = get_schema_store(
        BASEDIR.parent / "src" / "rpdk" / "core" / "data" / "schema"
    )
    assert len(schema_store) == 7
    assert "http://json-schema.org/draft-07/schema#" in schema_store
    assert (
        "https://schema.cloudformation.us-east-1.amazonaws.com/base.definition.schema.v1.json"
        in schema_store
    )
    assert (
        "https://schema.cloudformation.us-east-1.amazonaws.com/provider.configuration.definition.schema.v1.json"
        in schema_store
    )
    assert (
        "https://schema.cloudformation.us-east-1.amazonaws.com/provider.definition.schema.v1.json"
        in schema_store
    )
    assert (
        "https://schema.cloudformation.us-east-1.amazonaws.com/provider.definition.schema.hooks.v1.json"
        in schema_store
    )
    assert (
        "https://schema.cloudformation.us-east-1.amazonaws.com/provider.configuration.definition.schema.hooks.v1.json"
        in schema_store
    )


def test_get_schema_store_schemas_with_out_id():
    schema_store = get_schema_store(
        BASEDIR.parent / "src" / "rpdk" / "core" / "data" / "examples" / "resource"
    )
    assert len(schema_store) == 0
