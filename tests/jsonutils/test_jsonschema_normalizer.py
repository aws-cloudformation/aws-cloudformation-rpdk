# fixture and parameter have the same name
# pylint: disable=redefined-outer-name
# pylint: disable=protected-access
import pytest

from rpdk.data_loaders import resource_json
from rpdk.jsonutils.jsonschema_normalizer import (
    ConstraintError,
    JsonSchemaNormalizer,
    NormalizationError,
)


@pytest.fixture
def test_provider_schema():
    return resource_json(__name__, "data/area_definition.json")


@pytest.fixture
def normalized_schema():
    return resource_json(__name__, "data/area_definition_normalized.json")


@pytest.fixture
def normalizer(test_provider_schema):
    return JsonSchemaNormalizer(test_provider_schema)


PRIMITIVE_TYPES = [
    {"type": "string"},
    {"type": "integer"},
    {"type": "number"},
    {"type": "object"},
    {"type": "array"},
    {"type": "boolean"},
]
UNIQUE_KEY = "OWSAZD"


def test_normalizer(normalizer, normalized_schema):
    normalized_schema_map = normalizer.collapse_and_resolve_schema()

    assert normalized_schema == normalized_schema_map


@pytest.mark.parametrize("primitive_type", PRIMITIVE_TYPES)
def test_walk_primitive_type(primitive_type):
    normalizer = JsonSchemaNormalizer({})
    result = normalizer._walk("", primitive_type)

    assert result == primitive_type
    assert not normalizer._schema_map


@pytest.mark.parametrize("primitive_type", PRIMITIVE_TYPES)
def test_walk_ref_to_primitive_type(primitive_type):
    normalizer = JsonSchemaNormalizer({"definitions": primitive_type})
    result = normalizer._walk("", {"$ref": "#/definitions"})

    assert result == primitive_type
    assert not normalizer._schema_map


def test_walk_path_already_processed():
    normalizer = JsonSchemaNormalizer({})
    ref = "#/properties/City"
    normalizer._schema_map = {ref: None}
    result = normalizer._walk(ref, None)

    assert result == {"$ref": ref}
    assert len(normalizer._schema_map) == 1


def test_collapse_ref_type(normalizer, normalized_schema):
    schema_path = "#/definitions/boundary/properties/box/properties/north"
    expected_collapsed_schema = {"$ref": "#/definitions/coordinate"}
    coordinate_path = "#/definitions/coordinate"

    collapsed_schema = normalizer._collapse_ref_type(schema_path)

    assert expected_collapsed_schema == collapsed_schema
    assert normalizer._schema_map[coordinate_path] == normalized_schema[coordinate_path]
    assert len(normalizer._schema_map) == 1


def test_collapse_ref_type_nested(normalizer, normalized_schema):
    schema_path = "#/definitions/boundary/properties/box"
    expected_collapsed_schema = {"$ref": "#/definitions/boundary/properties/box"}
    coordinate_path = "#/definitions/coordinate"

    collapsed_schema = normalizer._collapse_ref_type(schema_path)

    assert expected_collapsed_schema == collapsed_schema
    assert normalizer._schema_map[schema_path] == normalized_schema[schema_path]
    assert (
        normalizer._schema_map[coordinate_path]
        == normalized_schema["#/definitions/coordinate"]
    )
    assert len(normalizer._schema_map) == 2


def test_circular_reference():
    schema_normalized = resource_json(
        __name__, "data/circular_reference_normalized.json"
    )
    schema = resource_json(__name__, "data/circular_reference.json")
    resolved_schema = JsonSchemaNormalizer(schema).collapse_and_resolve_schema()
    assert resolved_schema == schema_normalized


def test_collapse_array_type(normalizer, normalized_schema):
    property_key = "#/properties/city/properties/neighborhoods"
    unresolved_schema = normalizer._find_subschema_by_ref(property_key)
    resolved_schema = normalizer._collapse_array_type(property_key, unresolved_schema)
    new_key = "#/properties/city/properties/neighborhoods/items/patternProperties/%5BA-Za-z0-9%5D%7B1%2C64%7D"  # noqa: B950 pylint:disable=line-too-long
    expected_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "patternProperties": {"[A-Za-z0-9]{1,64}": {"$ref": new_key}},
            "insertionOrder": True,
        },
    }
    assert resolved_schema == expected_schema
    assert normalizer._schema_map[new_key] == normalized_schema[new_key]
    assert len(normalizer._schema_map) == 1


def test_find_schema_from_ref(normalizer, test_provider_schema):
    location_schema_expected = {
        "type": "object",
        "properties": {
            "country": {"type": "string"},
            "boundary": {"$ref": "#/definitions/boundary"},
        },
    }
    location_schema = normalizer._find_subschema_by_ref("#/definitions/location")
    assert location_schema == location_schema_expected

    expected_street_schema = {"type": "string"}
    street_schema = normalizer._find_subschema_by_ref(
        "#/properties/city/properties/neighborhoods/items/patternProperties/%5BA-Za-z0-9%5D%7B1%2C64%7D/properties/street"  # noqa: B950 pylint:disable=line-too-long
    )
    assert street_schema == expected_street_schema

    assert normalizer._find_subschema_by_ref("#") == test_provider_schema

    ref = "#/this/is/not/a/path"
    with pytest.raises(NormalizationError) as excinfo:
        normalizer._find_subschema_by_ref(ref)
    assert ref in str(excinfo.value)


def test_schema_with_allof():
    schema = resource_json(__name__, "data/test_schema_allof.json")
    expected_normalized_schema = resource_json(
        __name__, "data/test_schema_allof_normalized.json"
    )
    normalizer = JsonSchemaNormalizer(schema)
    normalized_schema = normalizer.collapse_and_resolve_schema()
    assert normalized_schema == expected_normalized_schema


def test_walk_oneof_becomes_invalid():
    test_schema = {
        "properties": {"Foo": {"type": "string"}},
        "anyOf": [{"patternProperties": {"Test": {"type": "string"}}}],
    }
    normalizer = JsonSchemaNormalizer({})
    test_key = "#/properties/Foo"
    with pytest.raises(ConstraintError) as excinfo:
        normalizer._walk(test_key, test_schema)
    assert "mutually exclusive" in str(excinfo.value)


def test_overwrite_ref():
    expected_schema = {"properties": {"Test": {"$ref": "#/definitions/Other"}}}
    test_schema = {
        "properties": {"Test": {"$ref": "#/definitions/Id"}},
        "anyOf": [{"properties": {"Test": {"$ref": "#/definitions/Other"}}}],
    }
    test_key = "#/properties/Foo"
    normalizer = JsonSchemaNormalizer({})
    normalizer._schema_map = {"#/definitions/Other": {}}
    squashed_schema = normalizer._squash_array_keys(test_key, test_schema)
    assert expected_schema == squashed_schema
    assert len(normalizer._schema_map) == 1


def test_overwrite_type():
    expected_schema = {"properties": {"Test": {"type": "integer"}}}
    test_schema = {
        "properties": {"Test": {"type": "object"}},
        "anyOf": [{"properties": {"Test": {"type": "integer"}}}],
    }
    validate_squash_array_key(test_schema, expected_schema)


def test_squash_array_keys_pattern_properties():
    expected_schema = {
        "patternProperties": {"test": {"type": "object", "$ref": "#/definitions"}},
        "required": ["Foo"],
    }
    test_schema = {
        "patternProperties": {"test": {"$ref": "#/definitions"}},
        "anyOf": [
            {"patternProperties": {"test": {"type": "object"}}},
            {"required": ["Foo"]},
        ],
    }
    validate_squash_array_key(test_schema, expected_schema)


def test_squash_array_keys_objects():
    expected_schema = {
        "type": "object",
        "properties": {
            "Baz": {"type": "array"},
            "Foo": {"type": "string", "pattern": "[a-z]+"},
            "Bar": {"type": "number"},
        },
    }
    test_schemas = [
        {
            "type": "object",
            "properties": {"Baz": {"type": "array"}},
            "allOf": [{"properties": {"Foo": {"type": "string"}}}],
            "oneOf": [{"properties": {"Foo": {"type": "string", "pattern": "[a-z]+"}}}],
            "anyOf": [{"properties": {"Bar": {"type": "number"}}}],
        }
    ]
    for arr_key in ("anyOf", "oneOf", "allOf"):
        test_schemas.append(
            {
                "type": "object",
                "properties": {"Baz": {"type": "array"}},
                arr_key: [
                    {"properties": {"Foo": {"type": "string"}}},
                    {
                        "properties": {
                            "Foo": {"type": "string", "pattern": "[a-z]+"},
                            "Bar": {"type": "number"},
                        }
                    },
                ],
            }
        )

    for test_schema in test_schemas:
        validate_squash_array_key(test_schema, expected_schema)


def validate_squash_array_key(test_schema, expected_schema):
    test_key = "#/properties/Foo"
    normalizer = JsonSchemaNormalizer({})
    squashed_schema = normalizer._squash_array_keys(test_key, test_schema)
    assert expected_schema == squashed_schema
    assert not normalizer._schema_map


def test_contraint_array_additional_items_valid():
    normalizer = JsonSchemaNormalizer({})
    schema = {}
    result = normalizer._collapse_array_type(UNIQUE_KEY, schema)
    assert result == schema


def test_contraint_array_additional_items_invalid():
    normalizer = JsonSchemaNormalizer({})
    schema = {"additionalItems": {"type": "string"}}
    with pytest.raises(ConstraintError) as excinfo:
        normalizer._collapse_array_type(UNIQUE_KEY, schema)
    assert UNIQUE_KEY in str(excinfo.value)


def test_contraint_object_additional_properties_valid():
    normalizer = JsonSchemaNormalizer({})
    schema = {}
    result = normalizer._collapse_object_type(UNIQUE_KEY, schema)
    assert result == schema


def test_contraint_object_additional_properties_invalid():
    normalizer = JsonSchemaNormalizer({})
    schema = {"additionalProperties": {"type": "string"}}
    with pytest.raises(ConstraintError) as excinfo:
        normalizer._collapse_object_type(UNIQUE_KEY, schema)
    assert UNIQUE_KEY in str(excinfo.value)


def test_contraint_object_properties_and_pattern_properties():
    normalizer = JsonSchemaNormalizer({})
    schema = {
        "properties": {"foo": {"type": "string"}},
        "patternProperties": {"type": "string"},
    }
    with pytest.raises(ConstraintError) as excinfo:
        normalizer._collapse_object_type(UNIQUE_KEY, schema)
    assert UNIQUE_KEY in str(excinfo.value)
