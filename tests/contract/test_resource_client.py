# fixture and parameter have the same name
# pylint: disable=redefined-outer-name,protected-access
import logging
import time
from io import StringIO
from unittest.mock import ANY, patch

import pytest

import rpdk.core.contract.resource_client as rclient
from rpdk.core.boto_helpers import LOWER_CAMEL_CRED_KEYS
from rpdk.core.contract.interface import Action, HandlerErrorCode, OperationStatus
from rpdk.core.contract.resource_client import (
    ResourceClient,
    override_properties,
    prune_properties,
    prune_properties_from_model,
    prune_properties_if_not_exist_in_path,
    prune_properties_which_dont_exist_in_path,
)
from rpdk.core.contract.suite.resource.handler_commons import error_test_model_in_list
from rpdk.core.exceptions import InvalidProjectError
from rpdk.core.test import (
    DEFAULT_ENDPOINT,
    DEFAULT_FUNCTION,
    DEFAULT_REGION,
    empty_override,
)

EMPTY_OVERRIDE = empty_override()
ACCOUNT = "11111111"
LOG = logging.getLogger(__name__)

SCHEMA = {
    "properties": {
        "a": {"type": "number", "const": 1},
        "b": {"type": "number", "const": 2},
        "c": {"type": "number", "const": 3},
        "d": {"type": "number", "const": 4},
    },
    "readOnlyProperties": ["/properties/b"],
    "createOnlyProperties": ["/properties/c"],
    "primaryIdentifier": ["/properties/c"],
    "writeOnlyProperties": ["/properties/d"],
    "handlers": {"create": {}, "delete": {}, "read": {}},
}

SCHEMA_WITH_MULTIPLE_WRITE_PROPERTIES = {
    "properties": {
        "a": {"type": "number", "const": 1},
        "b": {"type": "number", "const": 2},
        "c": {"type": "number", "const": 3},
        "d": {"type": "number", "const": 4},
    },
    "readOnlyProperties": ["/properties/b"],
    "createOnlyProperties": ["/properties/c"],
    "primaryIdentifier": ["/properties/c"],
    "writeOnlyProperties": ["/properties/d", "/properties/a"],
    "handlers": {"create": {}, "delete": {}, "read": {}},
}

SCHEMA_ = {
    "properties": {
        "a": {"type": "number"},
        "b": {"type": "number"},
        "c": {"type": "number"},
        "d": {"type": "number"},
    },
    "readOnlyProperties": ["/properties/b"],
    "createOnlyProperties": ["/properties/c"],
    "primaryIdentifier": ["/properties/c"],
    "writeOnlyProperties": ["/properties/d"],
    "handlers": {"create": {}, "delete": {}, "read": {}},
}

SCHEMA_WITH_NESTED_PROPERTIES = {
    "properties": {
        "a": {"type": "string"},
        "g": {"type": "number"},
        "b": {"$ref": "#/definitions/c"},
        "f": {
            "type": "array",
            "items": {"$ref": "#/definitions/c"},
        },
        "h": {
            "type": "array",
            "insertionOrder": "false",
            "items": {"$ref": "#/definitions/c"},
        },
        "i": {
            "type": "array",
            "insertionOrder": "false",
            "items": "string",
        },
    },
    "definitions": {
        "c": {
            "type": "object",
            "properties": {"d": {"type": "integer"}, "e": {"type": "integer"}},
        }
    },
    "readOnlyProperties": ["/properties/a"],
    "primaryIdentifier": ["/properties/a"],
    "writeOnlyProperties": ["/properties/g"],
    "handlers": {"create": {}, "delete": {}, "read": {}},
}

SCHEMA_WITH_COMPOSITE_KEY = {
    "properties": {
        "a": {"type": "number"},
        "b": {"type": "number"},
        "c": {"type": "number"},
        "d": {"type": "number"},
    },
    "readOnlyProperties": ["/properties/d"],
    "createOnlyProperties": ["/properties/c"],
    "primaryIdentifier": ["/properties/c", "/properties/d"],
    "handlers": {"create": {}, "delete": {}, "read": {}},
}

SCHEMA_WITH_ADDITIONAL_IDENTIFIERS = {
    "properties": {
        "a": {"type": "number"},
        "b": {"type": "number"},
        "c": {"type": "number"},
        "d": {"type": "number"},
    },
    "readOnlyProperties": ["/properties/b"],
    "createOnlyProperties": ["/properties/c"],
    "primaryIdentifier": ["/properties/c"],
    "additionalIdentifiers": [["/properties/b"]],
    "handlers": {"create": {}, "delete": {}, "read": {}},
}

SCHEMA_WITH_PROPERTY_TRANSFORM = {
    "properties": {
        "a": {"type": "string"},
        "b": {"$ref": "#/definitions/c"},
    },
    "definitions": {
        "c": {
            "type": "object",
            "properties": {"d": {"type": "String"}, "e": {"type": "integer"}},
        }
    },
    "readOnlyProperties": ["/properties/a"],
    "primaryIdentifier": ["/properties/a"],
    "handlers": {"create": {}, "delete": {}, "read": {}},
    "propertyTransform": {"/properties/b/c/d": '.b.c.d + "Test"'},
}

EMPTY_SCHEMA = {"handlers": {"create": [], "delete": [], "read": []}}

RESOURCE_MODEL_LIST = [{"Id": "abc123", "b": "2"}]


@pytest.fixture
def resource_client():
    endpoint = "https://"
    patch_sesh = patch(
        "rpdk.core.contract.resource_client.create_sdk_session", autospec=True
    )
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    patch_account = patch(
        "rpdk.core.contract.resource_client.get_account",
        autospec=True,
        return_value=ACCOUNT,
    )
    with patch_sesh as mock_create_sesh, patch_creds as mock_creds:
        with patch_account as mock_account:
            mock_sesh = mock_create_sesh.return_value
            mock_sesh.region_name = DEFAULT_REGION
            client = ResourceClient(
                DEFAULT_FUNCTION, endpoint, DEFAULT_REGION, EMPTY_SCHEMA, EMPTY_OVERRIDE
            )

    mock_sesh.client.assert_called_once_with("lambda", endpoint_url=endpoint)
    mock_creds.assert_called_once_with(mock_sesh, LOWER_CAMEL_CRED_KEYS, None, None)
    mock_account.assert_called_once_with(mock_sesh, {})
    assert client._function_name == DEFAULT_FUNCTION
    assert client._schema == EMPTY_SCHEMA
    assert client._overrides == EMPTY_OVERRIDE
    assert client.account == ACCOUNT

    return client


@pytest.fixture
def resource_client_no_handler():
    endpoint = "https://"
    patch_sesh = patch(
        "rpdk.core.contract.resource_client.create_sdk_session", autospec=True
    )
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    patch_account = patch(
        "rpdk.core.contract.resource_client.get_account",
        autospec=True,
        return_value=ACCOUNT,
    )
    with patch_sesh as mock_create_sesh, patch_creds as mock_creds:
        with patch_account as mock_account:
            mock_sesh = mock_create_sesh.return_value
            mock_sesh.region_name = DEFAULT_REGION
            client = ResourceClient(
                DEFAULT_FUNCTION, endpoint, DEFAULT_REGION, {}, EMPTY_OVERRIDE
            )

    mock_sesh.client.assert_called_once_with("lambda", endpoint_url=endpoint)
    mock_creds.assert_called_once_with(mock_sesh, LOWER_CAMEL_CRED_KEYS, None, None)
    mock_account.assert_called_once_with(mock_sesh, {})
    assert client._function_name == DEFAULT_FUNCTION
    assert client._schema == {}
    assert client._overrides == EMPTY_OVERRIDE
    assert client.account == ACCOUNT

    return client


@pytest.fixture
def resource_client_inputs():
    endpoint = "https://"
    patch_sesh = patch(
        "rpdk.core.contract.resource_client.create_sdk_session", autospec=True
    )
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    patch_account = patch(
        "rpdk.core.contract.resource_client.get_account",
        autospec=True,
        return_value=ACCOUNT,
    )
    with patch_sesh as mock_create_sesh, patch_creds as mock_creds:
        with patch_account as mock_account:
            mock_sesh = mock_create_sesh.return_value
            mock_sesh.region_name = DEFAULT_REGION
            client = ResourceClient(
                DEFAULT_FUNCTION,
                endpoint,
                DEFAULT_REGION,
                EMPTY_SCHEMA,
                EMPTY_OVERRIDE,
                {"CREATE": {"a": 1}, "UPDATE": {"a": 2}, "INVALID": {"b": 2}},
            )

    mock_sesh.client.assert_called_once_with("lambda", endpoint_url=endpoint)
    mock_creds.assert_called_once_with(mock_sesh, LOWER_CAMEL_CRED_KEYS, None, None)
    mock_account.assert_called_once_with(mock_sesh, {})

    assert client._function_name == DEFAULT_FUNCTION
    assert client._schema == EMPTY_SCHEMA
    assert client._overrides == EMPTY_OVERRIDE
    assert client.account == ACCOUNT

    return client


@pytest.fixture(params=[SCHEMA_, SCHEMA_WITH_ADDITIONAL_IDENTIFIERS])
def resource_client_inputs_schema(request):
    endpoint = "https://"
    patch_sesh = patch(
        "rpdk.core.contract.resource_client.create_sdk_session", autospec=True
    )
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    patch_account = patch(
        "rpdk.core.contract.resource_client.get_account",
        autospec=True,
        return_value=ACCOUNT,
    )
    with patch_sesh as mock_create_sesh, patch_creds as mock_creds:
        with patch_account as mock_account:
            mock_sesh = mock_create_sesh.return_value
            mock_sesh.region_name = DEFAULT_REGION
            client = ResourceClient(
                DEFAULT_FUNCTION,
                endpoint,
                DEFAULT_REGION,
                request.param,
                EMPTY_OVERRIDE,
                {
                    "CREATE": {"a": 111, "c": 2, "d": 3},
                    "UPDATE": {"a": 1, "c": 2},
                    "INVALID": {"c": 3},
                },
            )

    mock_sesh.client.assert_called_once_with("lambda", endpoint_url=endpoint)
    mock_creds.assert_called_once_with(mock_sesh, LOWER_CAMEL_CRED_KEYS, None, None)
    mock_account.assert_called_once_with(mock_sesh, {})

    assert client._function_name == DEFAULT_FUNCTION
    assert client._schema == request.param
    assert client._overrides == EMPTY_OVERRIDE
    assert client.account == ACCOUNT

    return client


@pytest.fixture
def resource_client_inputs_composite_key():
    endpoint = "https://"
    patch_sesh = patch(
        "rpdk.core.contract.resource_client.create_sdk_session", autospec=True
    )
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    patch_account = patch(
        "rpdk.core.contract.resource_client.get_account",
        autospec=True,
        return_value=ACCOUNT,
    )
    with patch_sesh as mock_create_sesh, patch_creds as mock_creds:
        with patch_account as mock_account:
            mock_sesh = mock_create_sesh.return_value
            mock_sesh.region_name = DEFAULT_REGION
            client = ResourceClient(
                DEFAULT_FUNCTION,
                endpoint,
                DEFAULT_REGION,
                SCHEMA_WITH_COMPOSITE_KEY,
                EMPTY_OVERRIDE,
                {
                    "CREATE": {"a": 111, "c": 2},
                    "UPDATE": {"a": 1, "c": 2},
                    "INVALID": {"c": 3},
                },
            )

    mock_sesh.client.assert_called_once_with("lambda", endpoint_url=endpoint)
    mock_creds.assert_called_once_with(mock_sesh, LOWER_CAMEL_CRED_KEYS, None, None)
    mock_account.assert_called_once_with(mock_sesh, {})

    assert client._function_name == DEFAULT_FUNCTION
    assert client._schema == SCHEMA_WITH_COMPOSITE_KEY
    assert client._overrides == EMPTY_OVERRIDE
    assert client.account == ACCOUNT

    return client


@pytest.fixture
def resource_client_inputs_property_transform():
    endpoint = "https://"
    patch_sesh = patch(
        "rpdk.core.contract.resource_client.create_sdk_session", autospec=True
    )
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    patch_account = patch(
        "rpdk.core.contract.resource_client.get_account",
        autospec=True,
        return_value=ACCOUNT,
    )
    with patch_sesh as mock_create_sesh, patch_creds as mock_creds:
        with patch_account as mock_account:
            mock_sesh = mock_create_sesh.return_value
            mock_sesh.region_name = DEFAULT_REGION
            client = ResourceClient(
                DEFAULT_FUNCTION,
                endpoint,
                DEFAULT_REGION,
                SCHEMA_WITH_PROPERTY_TRANSFORM,
                EMPTY_OVERRIDE,
            )

    mock_sesh.client.assert_called_once_with("lambda", endpoint_url=endpoint)
    mock_creds.assert_called_once_with(mock_sesh, LOWER_CAMEL_CRED_KEYS, None, None)
    mock_account.assert_called_once_with(mock_sesh, {})
    assert client._function_name == DEFAULT_FUNCTION
    assert client._schema == SCHEMA_WITH_PROPERTY_TRANSFORM
    assert client._overrides == EMPTY_OVERRIDE
    assert client.account == ACCOUNT

    return client


def test_error_test_model_in_list(resource_client):
    patch_resource_model_list = patch(
        "rpdk.core.contract.suite.resource.handler_commons.get_resource_model_list",
        autospec=True,
        return_value=RESOURCE_MODEL_LIST,
    )
    resource_client.primary_identifier_paths = {("properties", "Id")}
    current_resource_model = {"Id": "xyz456", "b": "2"}
    with patch_resource_model_list:
        assertion_error_message = error_test_model_in_list(
            resource_client, current_resource_model, ""
        )
        assert (
            "abc123 does not match with Current Resource Model primary identifier"
            " xyz456" in assertion_error_message
        )


def test_get_primary_identifier_success():
    primary_identifier_path = {("properties", "a")}
    model = {"a": 1, "b": 3, "c": 4}
    plist = rclient.ResourceClient.get_primary_identifier(
        primary_identifier_path, model
    )
    assert plist[0] == 1


def test_get_primary_identifier_fail():
    primary_identifier_path = {("properties", "a")}
    model = {"b": 3, "c": 4}
    try:
        rclient.ResourceClient.get_primary_identifier(primary_identifier_path, model)
    except AssertionError:
        logging.debug("This test expects Assertion Exception to be thrown")


def test_prune_properties():
    document = {
        "foo": "bar",
        "spam": "eggs",
        "one": "two",
        "array": ["first", "second"],
    }
    prune_properties(document, [("foo",), ("spam",), ("not_found",), ("array", "1")])
    assert document == {"one": "two", "array": ["first"]}


def test_prune_properties_for_all_sequence_members():
    document: dict = {
        "foo": "bar",
        "spam": "eggs",
        "one": "two",
        "array": ["first", "second"],
    }
    prune_properties(
        document,
        [
            ("foo",),  # prune foo: bar
            ("spam",),  # prune spam: eggs
            ("not_found",),  # missing members are fine
            (
                "not_found",  # missing sequences are fine
                "*",
            ),
            (
                "array",  # prune members of sequence "array"
                "*",
            ),
        ],
    )
    assert document == {"one": "two", "array": []}


def test_prune_properties_nested_sequence():
    document: dict = {
        "array": [
            {
                "outer1": {"inner1": "valueA", "inner2": "valueA"},
                "outer2": ["valueA", "valueB"],
            },
            {
                "outer1": {"inner1": "valueB", "inner2": "valueB"},
                "outer2": ["valueC", "valueD"],
            },
        ],
    }
    prune_properties(
        document,
        [
            (
                "not_found",
                "*",
                "not_found",
                "*",
            ),
            (
                "array",
                "*",
                "outer1",
                "inner1",
            ),
            (
                "array",
                "*",
                "outer2",
                "*",
            ),
        ],
    )
    assert document == {
        "array": [
            {"outer1": {"inner2": "valueA"}, "outer2": []},
            {"outer1": {"inner2": "valueB"}, "outer2": []},
        ]
    }


def test_prune_properties_nested_sequence_2():
    document: dict = {
        "array": [
            {
                "array2": [{"i1": "A", "i2": "B"}, {"i1": "C", "i2": "D"}],
                "outer1": {"inner1": "valueA", "inner2": "valueA"},
                "outer2": ["valueA", "valueB"],
            },
            {
                "array2": [{"i1": "E", "i2": "F"}, {"i1": "G", "i2": "H"}],
                "outer1": {"inner1": "valueB", "inner2": "valueB"},
                "outer2": ["valueC", "valueD"],
            },
        ],
    }
    prune_properties(
        document,
        [
            (
                "not_found",
                "*",
                "not_found",
                "*",
            ),
            (
                "array",
                "*",
                "outer1",
                "inner1",
            ),
            (
                "array",
                "*",
                "outer2",
                "*",
            ),
            (
                "array",
                "1",
                "1",
                "i1",
            ),
        ],
    )
    assert document == {
        "array": [
            {
                "array2": [{"i1": "A", "i2": "B"}, {"i1": "C", "i2": "D"}],
                "outer1": {"inner2": "valueA"},
                "outer2": [],
            },
            {
                "array2": [{"i1": "E", "i2": "F"}, {"i1": "G", "i2": "H"}],
                "outer1": {"inner2": "valueB"},
                "outer2": [],
            },
        ]
    }


def test_prune_properties_specific_sequence_indices():
    document: dict = {
        "array": [
            {
                "outer1": {"inner1": "valueA", "inner2": "valueA"},
                "outer2": ["valueA", "valueB"],
            },
            {
                "outer1": {"inner1": "valueB", "inner2": "valueB"},
                "outer2": ["valueC", "valueD"],
            },
        ],
    }
    prune_properties(
        document,
        [
            (
                "array",
                "0",
                "outer1",
                "inner1",
            ),
            (
                "array",
                "1",
                "outer2",
                "1",
            ),
        ],
    )
    assert document == {
        "array": [
            {"outer1": {"inner2": "valueA"}, "outer2": ["valueA", "valueB"]},
            {"outer1": {"inner1": "valueB", "inner2": "valueB"}, "outer2": ["valueC"]},
        ]
    }


def test_prune_properties_from_model():
    document = {
        "foo": "bar",
        "spam": "eggs",
        "one": "two",
        "array": ["first", "second"],
    }
    prune_properties_from_model(
        document,
        [
            ("properties", "foo"),
            ("properties", "spam"),
            ("properties", "not_found"),
            ("properties", "array", "1"),
        ],
    )
    assert document == {"one": "two", "array": ["first"]}


def test_prune_properties_if_not_exist_in_path():
    previous_model = {
        "spam": "eggs",
        "one": "two",
        "array": ["first", "second"],
    }
    model = {
        "foo": "bar",
        "spam": "eggs",
        "one": "two",
        "array": ["first", "second"],
    }
    model = prune_properties_if_not_exist_in_path(
        model,
        previous_model,
        [
            ("properties", "foo"),
            ("properties", "spam"),
            ("properties", "array", "1"),
            ("properties", "invalid"),
        ],
    )
    assert model == previous_model


def test_prune_properties_which_dont_exist_in_path():
    model = {
        "spam": "eggs",
        "one": "two",
        "array": ["first", "second"],
    }
    model1 = prune_properties_which_dont_exist_in_path(
        model,
        [
            ("properties", "one"),
        ],
    )
    assert model1 == {"one": "two"}


def test_init_sam_cli_client():
    patch_sesh = patch(
        "rpdk.core.contract.resource_client.create_sdk_session", autospec=True
    )
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    patch_account = patch(
        "rpdk.core.contract.resource_client.get_account",
        autospec=True,
        return_value=ACCOUNT,
    )
    with patch_sesh as mock_create_sesh, patch_creds as mock_creds:
        with patch_account as mock_account:
            mock_sesh = mock_create_sesh.return_value
            mock_sesh.region_name = DEFAULT_REGION
            client = ResourceClient(
                DEFAULT_FUNCTION, DEFAULT_ENDPOINT, DEFAULT_REGION, {}, EMPTY_OVERRIDE
            )

    mock_sesh.client.assert_called_once_with(
        "lambda", endpoint_url=DEFAULT_ENDPOINT, use_ssl=False, verify=False, config=ANY
    )
    mock_creds.assert_called_once_with(mock_sesh, LOWER_CAMEL_CRED_KEYS, None, None)
    mock_account.assert_called_once_with(mock_sesh, {})
    assert client.account == ACCOUNT


def test_generate_token():
    token = ResourceClient.generate_token()
    assert isinstance(token, str)
    assert len(token) == 36


@pytest.mark.parametrize("resource_type", [None, "Org::Srv::Type"])
@pytest.mark.parametrize("log_group_name", [None, "random_name"])
@pytest.mark.parametrize(
    "log_creds",
    [
        {},
        {
            "AccessKeyId": object(),
            "SecretAccessKey": object(),
            "SessionToken": object(),
        },
    ],
)
def test_make_request(resource_type, log_group_name, log_creds):
    desired_resource_state = object()
    previous_resource_state = object()
    token = object()
    request = ResourceClient.make_request(
        desired_resource_state,
        previous_resource_state,
        "us-east-1",
        ACCOUNT,
        "CREATE",
        {},
        resource_type,
        log_group_name,
        log_creds,
        token,
    )
    expected_request = {
        "requestData": {
            "callerCredentials": {},
            "resourceProperties": desired_resource_state,
            "previousResourceProperties": previous_resource_state,
            "logicalResourceId": token,
            "typeConfiguration": None,
        },
        "region": DEFAULT_REGION,
        "awsAccountId": ACCOUNT,
        "action": "CREATE",
        "bearerToken": token,
        "callbackContext": None,
        "resourceType": resource_type,
    }
    if log_group_name and log_creds:
        expected_request["requestData"]["providerCredentials"] = log_creds
        expected_request["requestData"]["providerLogGroupName"] = log_group_name
    assert request == expected_request


def test_get_metadata(resource_client):
    schema = {
        "properties": {
            "a": {"type": "array", "const": 1, "insertionOrder": "true"},
            "b": {"type": "number", "const": 2, "insertionOrder": "false"},
            "c": {"type": "number", "const": 3},
            "d": {"type": "number", "const": 4},
        },
        "readOnlyProperties": ["/properties/c"],
        "createOnlyProperties": ["/properties/d"],
    }
    resource_client._update_schema(schema)
    assert resource_client.get_metadata() == {"b"}


def test_update_schema(resource_client):
    resource_client._strategy = object()

    schema = {
        "primaryIdentifier": ["/properties/a"],
        "readOnlyProperties": ["/properties/b"],
        "writeOnlyProperties": ["/properties/c"],
        "createOnlyProperties": ["/properties/d"],
    }
    resource_client._update_schema(schema)

    assert resource_client._schema is schema
    assert resource_client._strategy is None
    assert resource_client.primary_identifier_paths == {("properties", "a")}
    assert resource_client.read_only_paths == {("properties", "b")}
    assert resource_client.write_only_paths == {("properties", "c")}
    assert resource_client.create_only_paths == {("properties", "d")}


def test_transform_model(resource_client_inputs_property_transform):
    inputs = {"a": "ValueA", "b": {"c": {"d": "ValueD", "e": 1}}}
    expected_inputs = {"a": "ValueA", "b": {"c": {"d": "ValueDTest", "e": 1}}}

    transformed_inputs = resource_client_inputs_property_transform.transform_model(
        inputs
    )

    assert transformed_inputs == expected_inputs


def test_compare_with_transform_should_pass(resource_client_inputs_property_transform):
    inputs = {"a": "ValueA", "b": {"c": {"d": "ValueD", "e": 1}}}
    # transformed_inputs = {"a": "ValueA", "b": {"c": {"d": "ValueDTest", "e": 1}}}
    outputs = {"a": "ValueA", "b": {"c": {"d": "ValueDTest", "e": 1}}}

    resource_client_inputs_property_transform.compare(inputs, outputs)


def test_compare_with_transform_should_throw_exception(
    resource_client_inputs_property_transform,
):
    inputs = {"a": "ValueA", "b": {"c": {"d": "ValueD", "e": 1}}}
    outputs = {"a": "ValueA", "b": {"c": {"d": "D", "e": 1}}}

    try:
        resource_client_inputs_property_transform.compare(inputs, outputs)
    except AssertionError:
        logging.debug("This test expects Assertion Exception to be thrown")


def test_strategy(resource_client):
    schema = {
        "properties": {
            "a": {"type": "number", "const": 1},
            "b": {"type": "number", "const": 2},
            "c": {"type": "number", "const": 3},
            "d": {"type": "number", "const": 4},
        },
        "readOnlyProperties": ["/properties/c"],
        "createOnlyProperties": ["/properties/d"],
    }
    resource_client._update_schema(schema)

    assert resource_client._schema is schema
    assert resource_client._strategy is None

    strategy = resource_client.strategy

    assert resource_client._strategy is strategy
    assert strategy.example() == {"a": 1, "b": 2, "d": 4}

    cached = resource_client.strategy

    assert cached is strategy
    assert resource_client._strategy is strategy


def test_invalid_strategy(resource_client):
    schema = {
        "properties": {
            "a": {"type": "number", "const": 1},
            "b": {"type": "number", "const": 2},
            "c": {"type": "number", "const": 3},
            "d": {"type": "number", "const": 4},
        },
        "readOnlyProperties": ["/properties/c"],
        "createOnlyProperties": ["/properties/d"],
    }
    resource_client._update_schema(schema)

    assert resource_client._schema is schema
    assert resource_client._strategy is None

    invalid_strategy = resource_client.invalid_strategy

    assert resource_client._invalid_strategy is invalid_strategy
    assert invalid_strategy.example() == {"a": 1, "b": 2, "c": 3, "d": 4}

    cached = resource_client.invalid_strategy

    assert cached is invalid_strategy
    assert resource_client._invalid_strategy is invalid_strategy


def test_update_strategy(resource_client):
    schema = {
        "properties": {
            "a": {"type": "number", "const": 1},
            "b": {"type": "number", "const": 2},
            "c": {"type": "number", "const": 3},
            "d": {"type": "number", "const": 4},
        },
        "readOnlyProperties": ["/properties/c"],
        "createOnlyProperties": ["/properties/d"],
    }
    resource_client._update_schema(schema)

    assert resource_client._schema is schema
    assert resource_client._update_strategy is None

    update_strategy = resource_client.update_strategy

    assert resource_client._update_strategy is update_strategy
    assert update_strategy.example() == {"a": 1, "b": 2}

    cached = resource_client.update_strategy

    assert cached is update_strategy
    assert resource_client._update_strategy is update_strategy


def test_generate_create_example(resource_client):
    schema = {
        "properties": {
            "a": {"type": "number", "const": 1},
            "b": {"type": "number", "const": 2},
        },
        "readOnlyProperties": ["/properties/b"],
    }
    resource_client._update_schema(schema)
    example = resource_client.generate_create_example()
    assert example == {"a": 1}


def test_generate_invalid_create_example(resource_client):
    schema = {
        "properties": {
            "a": {"type": "number", "const": 1},
            "b": {"type": "number", "const": 2},
        },
        "readOnlyProperties": ["/properties/b"],
    }
    resource_client._update_schema(schema)
    example = resource_client.generate_invalid_create_example()
    assert example == {"a": 1, "b": 2}


def test_generate_update_example(resource_client):
    schema = {
        "properties": {
            "a": {"type": "number", "const": 1},
            "b": {"type": "number", "const": 2},
            "c": {"type": "number", "const": 3},
        },
        "readOnlyProperties": ["/properties/b"],
        "createOnlyProperties": ["/properties/c"],
    }
    resource_client._update_schema(schema)
    resource_client._overrides = {}
    model_from_created_resource = {"b": 2, "a": 4}
    example = resource_client.generate_update_example(model_from_created_resource)
    assert example == {"a": 1, "b": 2}


def test_generate_invalid_update_example(resource_client):
    schema = {
        "properties": {
            "a": {"type": "number", "const": 1},
            "b": {"type": "number", "const": 2},
            "c": {"type": "number", "const": 3},
        },
        "readOnlyProperties": ["/properties/b"],
        "createOnlyProperties": ["/properties/c"],
    }
    resource_client._update_schema(schema)
    resource_client._overrides = {}
    model_from_created_resource = {"b": 2, "a": 4}
    example = resource_client.generate_invalid_update_example(
        model_from_created_resource
    )
    assert example == {"a": 1, "b": 2, "c": 3}


def test_generate_update_example_update_override(resource_client):
    schema = {
        "properties": {
            "a": {"type": "number", "const": 1},
            "b": {"type": "number", "const": 2},
            "c": {"type": "number", "const": 3},
        },
        "readOnlyProperties": ["/properties/b"],
        "createOnlyProperties": ["/properties/c"],
    }
    resource_client._update_schema(schema)
    overrides = {"UPDATE": {"a": 2}, "CREATE": {"a": 5}}
    resource_client._overrides = overrides
    model_from_created_resource = {"b": 2, "a": 4}
    example = resource_client.generate_update_example(model_from_created_resource)
    assert example == {"a": 2, "b": 2}


def test_generate_update_example_create_override(resource_client):
    schema = {
        "properties": {
            "a": {"type": "number", "const": 1},
            "b": {"type": "number", "const": 2},
            "c": {"type": "number", "const": 3},
        },
        "readOnlyProperties": ["/properties/b"],
        "createOnlyProperties": ["/properties/c"],
    }
    resource_client._update_schema(schema)
    overrides = {"CREATE": {"a": 5}}
    resource_client._overrides = overrides
    model_from_created_resource = {"b": 2, "a": 4}
    example = resource_client.generate_update_example(model_from_created_resource)
    assert example == {"a": 5, "b": 2}


def test_has_only_writable_identifiers_primary_is_read_only(resource_client):
    resource_client._update_schema(
        {
            "primaryIdentifier": ["/properties/foo"],
            "readOnlyProperties": ["/properties/foo"],
        }
    )

    assert not resource_client.has_only_writable_identifiers()


def test_has_only_writable_identifiers_primary_is_writable(resource_client):
    resource_client._update_schema(
        {
            "primaryIdentifier": ["/properties/foo"],
            "createOnlyProperties": ["/properties/foo"],
        }
    )

    assert resource_client.has_only_writable_identifiers()


def test_has_only_writable_identifiers_primary_and_additional_are_read_only(
    resource_client,
):
    resource_client._update_schema(
        {
            "primaryIdentifier": ["/properties/foo"],
            "additionalIdentifiers": [["/properties/bar"]],
            "readOnlyProperties": ["/properties/foo", "/properties/bar"],
        }
    )

    assert not resource_client.has_only_writable_identifiers()


def test_has_only_writable_identifiers_additional_is_writable(resource_client):
    resource_client._update_schema(
        {
            "primaryIdentifier": ["/properties/foo"],
            "additionalIdentifiers": [["/properties/bar"]],
            "readOnlyProperties": ["/properties/foo"],
        }
    )

    assert not resource_client.has_only_writable_identifiers()


def test_has_only_writable_identifiers_compound_is_writable(resource_client):
    resource_client._update_schema(
        {
            "primaryIdentifier": ["/properties/foo"],
            "additionalIdentifiers": [["/properties/bar", "/properties/baz"]],
            "readOnlyProperties": ["/properties/foo", "/properties/baz"],
        }
    )

    assert not resource_client.has_only_writable_identifiers()


def test_has_only_writable_identifiers_composite_primary_are_read_only(
    resource_client,
):
    resource_client._update_schema(
        {
            "primaryIdentifier": ["/properties/foo", "/properties/bar"],
            "readOnlyProperties": ["/properties/foo", "/properties/bar"],
        }
    )

    assert not resource_client.has_only_writable_identifiers()


def test_has_only_writable_identifiers_composite_primary_is_read_only(
    resource_client,
):
    resource_client._update_schema(
        {
            "primaryIdentifier": ["/properties/foo", "/properties/bar"],
            "readOnlyProperties": ["/properties/foo"],
            "createOnlyProperties": ["/properties/bar"],
        }
    )

    assert not resource_client.has_only_writable_identifiers()


def test_has_only_writable_identifiers_composite_primary_are_writable(
    resource_client,
):
    resource_client._update_schema(
        {
            "primaryIdentifier": ["/properties/foo", "/properties/bar"],
            "createOnlyProperties": ["/properties/foo", "/properties/bar"],
        }
    )

    assert resource_client.has_only_writable_identifiers()


def test_make_payload(resource_client):
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )

    token = "ecba020e-b2e6-4742-a7d0-8a06ae7c4b2f"
    with patch.object(
        resource_client, "generate_token", return_value=token
    ), patch_creds:
        payload = resource_client._make_payload("CREATE", {"foo": "bar"})

    assert payload == {
        "requestData": {
            "callerCredentials": {},
            "resourceProperties": {"foo": "bar"},
            "previousResourceProperties": None,
            "logicalResourceId": token,
            "typeConfiguration": None,
        },
        "region": DEFAULT_REGION,
        "awsAccountId": ACCOUNT,
        "action": "CREATE",
        "bearerToken": token,
        "callbackContext": None,
        "resourceType": None,
    }


def test_make_payload_with_stack_id(resource_client):
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )

    token = "ecba020e-b2e6-4742-a7d0-8a06ae7c4b2f"
    with patch.object(
        resource_client, "generate_token", return_value=token
    ), patch_creds:
        payload = resource_client._make_payload("CREATE", {"foo": "bar"}, stackId="test-stack")

    assert payload == {
        "requestData": {
            "callerCredentials": {},
            "resourceProperties": {"foo": "bar"},
            "previousResourceProperties": None,
            "logicalResourceId": token,
            "typeConfiguration": None,
        },
        "region": DEFAULT_REGION,
        "awsAccountId": ACCOUNT,
        "action": "CREATE",
        "bearerToken": token,
        "callbackContext": None,
        "resourceType": None,
        "stackId": "test-stack",
    }


@pytest.mark.parametrize("action", [Action.READ, Action.LIST])
def test_call_sync(resource_client, action):
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )

    mock_client = resource_client._client
    mock_client.invoke.return_value = {"Payload": StringIO('{"status": "SUCCESS"}')}
    with patch_creds:
        status, response = resource_client.call(action, {"resourceModel": SCHEMA})

    assert status == OperationStatus.SUCCESS
    assert response == {"status": OperationStatus.SUCCESS.value}


def test_call_docker():
    patch_sesh = patch(
        "rpdk.core.contract.resource_client.create_sdk_session", autospec=True
    )
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    patch_account = patch(
        "rpdk.core.contract.resource_client.get_account",
        autospec=True,
        return_value=ACCOUNT,
    )
    patch_docker = patch("rpdk.core.contract.resource_client.docker", autospec=True)
    with patch_sesh as mock_create_sesh, patch_docker as mock_docker, patch_creds:
        with patch_account:
            mock_client = mock_docker.from_env.return_value
            mock_sesh = mock_create_sesh.return_value
            mock_sesh.region_name = DEFAULT_REGION
            resource_client = ResourceClient(
                DEFAULT_FUNCTION,
                "url",
                DEFAULT_REGION,
                {},
                EMPTY_OVERRIDE,
                docker_image="docker_image",
                executable_entrypoint="entrypoint",
            )
    response_str = (
        "__CFN_RESOURCE_START_RESPONSE__"
        '{"status": "SUCCESS"}__CFN_RESOURCE_END_RESPONSE__'
    )
    mock_client.containers.run.return_value = str.encode(response_str)
    with patch_creds:
        status, response = resource_client.call("CREATE", {"resourceModel": SCHEMA})

    mock_client.containers.run.assert_called_once()
    assert status == OperationStatus.SUCCESS
    assert response == {"status": OperationStatus.SUCCESS.value}


def test_call_docker_executable_entrypoint_null():
    patch_sesh = patch(
        "rpdk.core.contract.resource_client.create_sdk_session", autospec=True
    )
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    patch_account = patch(
        "rpdk.core.contract.resource_client.get_account",
        autospec=True,
        return_value=ACCOUNT,
    )
    patch_docker = patch("rpdk.core.contract.resource_client.docker", autospec=True)
    with patch_sesh as mock_create_sesh, patch_docker, patch_creds:
        with patch_account:
            mock_sesh = mock_create_sesh.return_value
            mock_sesh.region_name = DEFAULT_REGION
            resource_client = ResourceClient(
                DEFAULT_FUNCTION,
                "url",
                DEFAULT_REGION,
                {},
                EMPTY_OVERRIDE,
                docker_image="docker_image",
            )

    try:
        with patch_creds:
            resource_client.call("CREATE", {"resourceModel": SCHEMA})
    except InvalidProjectError:
        pass


@pytest.mark.parametrize("action", [Action.CREATE, Action.UPDATE, Action.DELETE])
def test_call_async(resource_client, action):
    mock_client = resource_client._client

    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )

    mock_client.invoke.side_effect = [
        {"Payload": StringIO('{"status": "IN_PROGRESS", "resourceModel": {"c": 3} }')},
        {"Payload": StringIO('{"status": "SUCCESS"}')},
    ]

    with patch_creds:
        status, response = resource_client.call(action, {})

    assert status == OperationStatus.SUCCESS
    assert response == {"status": OperationStatus.SUCCESS.value}


@pytest.mark.parametrize("action", [Action.CREATE, Action.UPDATE, Action.DELETE])
def test_call_async_write_only_properties_are_removed(resource_client, action):
    mock_client = resource_client._client

    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )

    mock_client.invoke.side_effect = [
        {
            "Payload": StringIO(
                '{"status": "SUCCESS", "resourceModel": {"c": 3, "d": 4} }'
            )
        }
    ]

    resource_client._update_schema(SCHEMA)
    with pytest.raises(AssertionError), patch_creds:
        resource_client.call(action, {})


@pytest.mark.parametrize("action", [Action.CREATE, Action.UPDATE, Action.DELETE])
def test_call_async_write_only_properties_are_not_removed_for_in_progress(
    resource_client, action
):
    mock_client = resource_client._client

    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )

    mock_client.invoke.side_effect = [
        {
            "Payload": StringIO(
                '{"status": "IN_PROGRESS", "resourceModel": {"c": 3, "d": 4} }'
            )
        },
        {"Payload": StringIO('{"status": "SUCCESS"}')},
    ]

    resource_client._update_schema(SCHEMA)
    with patch_creds:
        resource_client.call(action, {})


def test_call_and_assert_success(resource_client):
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    mock_client = resource_client._client
    mock_client.invoke.return_value = {"Payload": StringIO('{"status": "SUCCESS"}')}
    with patch_creds:
        status, response, error_code = resource_client.call_and_assert(
            Action.CREATE, OperationStatus.SUCCESS, {}, None
        )
    assert status == OperationStatus.SUCCESS
    assert response == {"status": OperationStatus.SUCCESS.value}
    assert error_code is None


def test_call_and_assert_fails(resource_client_no_handler):
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    with patch_creds:
        try:
            resource_client_no_handler.call_and_assert(
                Action.CREATE, OperationStatus.SUCCESS, {}, None
            )
        except ValueError:
            LOG.debug(
                "Value Error Exception is expected when required CRD handlers are not"
                " present"
            )


def test_call_and_assert_failed_invalid_payload(resource_client):
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    mock_client = resource_client._client
    mock_client.invoke.return_value = {"Payload": StringIO("invalid json document")}

    with pytest.raises(ValueError), patch_creds:
        status, response, error_code = resource_client.call_and_assert(
            Action.CREATE, OperationStatus.SUCCESS, {}, None
        )


def test_call_and_assert_failed(resource_client):
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    mock_client = resource_client._client
    mock_client.invoke.return_value = {
        "Payload": StringIO('{"status": "FAILED","errorCode": "NotFound"}')
    }
    with patch_creds:
        status, response, error_code = resource_client.call_and_assert(
            Action.DELETE, OperationStatus.FAILED, {}, None
        )
    assert status == OperationStatus.FAILED
    assert response == {"status": OperationStatus.FAILED.value, "errorCode": "NotFound"}
    assert error_code == HandlerErrorCode.NotFound


def test_call_and_assert_exception_unsupported_status(resource_client):
    mock_client = resource_client._client
    mock_client.invoke.return_value = {
        "Payload": StringIO('{"status": "FAILED","errorCode": "NotFound"}')
    }
    with pytest.raises(ValueError):
        resource_client.call_and_assert(Action.DELETE, "OtherStatus", {}, None)


def test_call_and_assert_exception_assertion_mismatch(resource_client):
    patch_creds = patch(
        "rpdk.core.contract.resource_client.get_temporary_credentials",
        autospec=True,
        return_value={},
    )
    mock_client = resource_client._client
    mock_client.invoke.return_value = {"Payload": StringIO('{"status": "SUCCESS"}')}
    with pytest.raises(AssertionError), patch_creds:
        resource_client.call_and_assert(Action.CREATE, OperationStatus.FAILED, {}, None)


@pytest.mark.parametrize("status", [OperationStatus.SUCCESS, OperationStatus.FAILED])
def test_assert_in_progress_wrong_status(status):
    with pytest.raises(AssertionError):
        ResourceClient.assert_in_progress(status, {})


def test_assert_in_progress_error_code_set():
    with pytest.raises(AssertionError):
        ResourceClient.assert_in_progress(
            OperationStatus.IN_PROGRESS,
            {"errorCode": HandlerErrorCode.AccessDenied.value},
        )


def test_assert_in_progress_resource_models_set():
    with pytest.raises(AssertionError):
        ResourceClient.assert_in_progress(
            OperationStatus.IN_PROGRESS, {"resourceModels": []}
        )


def test_assert_in_progress_callback_delay_seconds_unset():
    callback_delay_seconds = ResourceClient.assert_in_progress(
        OperationStatus.IN_PROGRESS, {"resourceModels": None}
    )
    assert callback_delay_seconds == 0


def test_assert_in_progress_callback_delay_seconds_set():
    callback_delay_seconds = ResourceClient.assert_in_progress(
        OperationStatus.IN_PROGRESS, {"callbackDelaySeconds": 5}
    )
    assert callback_delay_seconds == 5


@pytest.mark.parametrize(
    "status", [OperationStatus.IN_PROGRESS, OperationStatus.FAILED]
)
def test_assert_success_wrong_status(status):
    with pytest.raises(AssertionError):
        ResourceClient.assert_success(status, {})


def test_assert_success_error_code_set():
    with pytest.raises(AssertionError):
        ResourceClient.assert_success(
            OperationStatus.SUCCESS, {"errorCode": HandlerErrorCode.AccessDenied.value}
        )


def test_assert_success_callback_delay_seconds_set():
    with pytest.raises(AssertionError):
        ResourceClient.assert_success(
            OperationStatus.SUCCESS, {"callbackDelaySeconds": 5}
        )


@pytest.mark.parametrize(
    "status", [OperationStatus.IN_PROGRESS, OperationStatus.SUCCESS]
)
def test_assert_failed_wrong_status(status):
    with pytest.raises(AssertionError):
        ResourceClient.assert_failed(status, {})


def test_assert_failed_error_code_unset():
    with pytest.raises(AssertionError):
        ResourceClient.assert_failed(OperationStatus.FAILED, {})


def test_assert_failed_error_code_invalid():
    with pytest.raises(KeyError):
        ResourceClient.assert_failed(OperationStatus.FAILED, {"errorCode": "XXX"})


def test_assert_failed_callback_delay_seconds_set():
    with pytest.raises(AssertionError):
        ResourceClient.assert_failed(
            OperationStatus.FAILED,
            {
                "errorCode": HandlerErrorCode.AccessDenied.value,
                "callbackDelaySeconds": 5,
            },
        )


def test_assert_failed_resource_models_set():
    with pytest.raises(AssertionError):
        ResourceClient.assert_failed(
            OperationStatus.FAILED,
            {"errorCode": HandlerErrorCode.AccessDenied.value, "resourceModels": []},
        )


def test_assert_failed_returns_error_code():
    error_code = ResourceClient.assert_failed(
        OperationStatus.FAILED, {"errorCode": HandlerErrorCode.AccessDenied.value}
    )
    assert error_code == HandlerErrorCode.AccessDenied


def test_override_properties():
    document = {
        "foo": "bar",
        "spam": "eggs",
        "one": "two",
        "array": ["first", "second"],
    }
    override_properties(
        document,
        {("foo",): "baz", ("spam",): {}, ("not_found",): None, ("array", "1"): "last"},
    )
    assert document == {
        "foo": "baz",
        "spam": {},
        "one": "two",
        "array": ["first", "last"],
    }


def test_has_update_handler(resource_client):
    schema = {"handlers": {"update": {"permissions": ["permission"]}}}
    resource_client._update_schema(schema)
    assert resource_client.has_update_handler()


def test_is_taggable(resource_client):
    schema = {"tagging": {"taggable": True}}
    resource_client._update_schema(schema)
    assert resource_client.is_taggable()


def test_is_taggable_default_value(resource_client):
    schema = {}
    resource_client._update_schema(schema)
    assert resource_client.is_taggable()


def test_is_tag_updatable(resource_client):
    schema = {"tagging": {"taggable": True, "tagUpdatable": True}}
    resource_client._update_schema(schema)
    assert resource_client.is_tag_updatable()


def test_contains_tagging_metadata(resource_client):
    schema = {"taggable": False}
    resource_client._update_schema(schema)
    assert not resource_client.contains_tagging_metadata()


def test_metadata_contains_tag_property(resource_client):
    schema = {"tagging": {"taggable": True, "tagProperty": "/properties/Tags"}}
    resource_client._update_schema(schema)
    assert resource_client.metadata_contains_tag_property()


@pytest.mark.parametrize(
    "schema,result",
    [
        ({"tagging": {"permissions": ["test:permission"]}}, ["test:permission"]),
        ({}, []),
    ],
)
def test_get_tagging_permission(resource_client, schema, result):
    resource_client._update_schema(schema)
    assert resource_client.get_tagging_permissions() == result


def test_validate_model_contain_tags(resource_client):
    schema = {"tagging": {"taggable": True, "tagProperty": "/properties/Tags"}}
    resource_client._update_schema(schema)
    inputs = {"Tags": [{"Key": "key1", "Value": "value1"}]}
    assert resource_client.validate_model_contain_tags(inputs)


@pytest.mark.parametrize("action", [Action.CREATE, Action.UPDATE, Action.DELETE])
def test_assert_CUD_time(resource_client, action):
    resource_client.assert_time(time.time() - 59, time.time(), action)


@pytest.mark.parametrize("action", [Action.READ, Action.LIST])
def test_assert_RL_time(resource_client, action):
    resource_client.assert_time(time.time() - 29, time.time(), action)


@pytest.mark.parametrize("action", [Action.CREATE, Action.UPDATE, Action.DELETE])
def test_assert_CUD_time_fail(resource_client, action):
    with pytest.raises(AssertionError):
        resource_client.assert_time(time.time() - 61, time.time(), action)


@pytest.mark.parametrize("action", [Action.READ, Action.LIST])
def test_assert_RL_time_fail(resource_client, action):
    with pytest.raises(AssertionError):
        resource_client.assert_time(time.time() - 31, time.time(), action)


def test_assert_primary_identifier_success(resource_client):
    resource_client._update_schema(SCHEMA)
    resource_client.assert_primary_identifier(
        resource_client.primary_identifier_paths, {"a": 1, "b": 2, "c": 3}
    )


def test_assert_primary_identifier_fail(resource_client):
    with pytest.raises(AssertionError):
        resource_client._update_schema(SCHEMA)
        resource_client.assert_primary_identifier(
            resource_client.primary_identifier_paths, {"a": 1, "b": 2}
        )


def test_is_primary_identifier_equal_success(resource_client):
    resource_client._update_schema(SCHEMA)
    assert resource_client.is_primary_identifier_equal(
        resource_client.primary_identifier_paths,
        {"a": 1, "b": 2, "c": 3},
        {"a": 1, "b": 2, "c": 3},
    )


def test_is_primary_identifier_equal_fail(resource_client):
    resource_client._update_schema(SCHEMA)
    assert not resource_client.is_primary_identifier_equal(
        resource_client.primary_identifier_paths,
        {"a": 1, "b": 2, "c": 3},
        {"a": 1, "b": 2, "c": 4},
    )


def test_is_primary_identifier_equal_fail_key(resource_client):
    with pytest.raises(AssertionError):
        resource_client._update_schema(SCHEMA)
        resource_client.is_primary_identifier_equal(
            resource_client.primary_identifier_paths,
            {"a": 1, "b": 2},
            {"a": 1, "b": 2},
        )


def test_assert_write_only_property_does_not_exist(resource_client):
    schema = {
        "a": {"type": "number", "const": 1},
        "b": {"type": "number", "const": 2},
        "c": {"type": "number", "const": 3},
    }
    resource_client._update_schema(schema)
    resource_client.assert_write_only_property_does_not_exist(schema)


@pytest.mark.parametrize("schema", [SCHEMA, SCHEMA_WITH_MULTIPLE_WRITE_PROPERTIES])
def test_assert_write_only_property_does_not_exist_success(resource_client, schema):
    created_resource = {"a": None, "b": 2, "c": 3}
    resource_client._update_schema(schema)
    resource_client.assert_write_only_property_does_not_exist(created_resource)


@pytest.mark.parametrize("schema", [SCHEMA, SCHEMA_WITH_MULTIPLE_WRITE_PROPERTIES])
def test_assert_write_only_property_does_not_exist_fail(resource_client, schema):
    with pytest.raises(AssertionError):
        created_resource = {"a": 1, "b": 2, "c": 3, "d": 4}
        resource_client._update_schema(schema)
        resource_client.assert_write_only_property_does_not_exist(created_resource)


def test_get_value_by_key_path_with_string_key(resource_client):
    model = {"a": 1, "b": 2}
    key_path = "a"
    assert resource_client.get_value_by_key_path(model, key_path) == 1


def test_generate_create_example_with_inputs(resource_client_inputs):
    assert resource_client_inputs.generate_create_example() == {"a": 1}


def test_generate_invalid_create_example_with_inputs(resource_client_inputs):
    assert resource_client_inputs.generate_invalid_create_example() == {"b": 2}


def test_generate_update_example_with_inputs(resource_client_inputs):
    assert resource_client_inputs.generate_update_example({"a": 1}) == {"a": 2}


def test_generate_invalid_update_example_with_inputs(resource_client_inputs):
    assert resource_client_inputs.generate_invalid_update_example({"a": 1}) == {"b": 2}


def test_generate_update_example_with_primary_identifier(resource_client_inputs_schema):
    created_resource = resource_client_inputs_schema.generate_create_example()
    # adding read only property to denote a realistic scenario
    created_resource["b"] = 2
    updated_resource = resource_client_inputs_schema.generate_update_example(
        created_resource
    )
    assert updated_resource == {"a": 1, "c": 2, "b": 2}


def test_generate_update_example_with_composite_key(
    resource_client_inputs_composite_key,
):
    created_resource = resource_client_inputs_composite_key.generate_create_example()
    created_resource.update({"d": 3})  # mocking value of d as it is a readOnly property
    updated_resource = resource_client_inputs_composite_key.generate_update_example(
        created_resource
    )
    assert updated_resource == {"a": 1, "c": 2, "d": 3}


def test_compare_should_pass(resource_client):
    resource_client._update_schema(SCHEMA_WITH_NESTED_PROPERTIES)
    inputs = {
        "b": {"d": 1},
        "f": [{"d": 1}],
        "h": [{"d": 1}, {"d": 2}],
        "i": ["abc", "ghi"],
    }

    outputs = {
        "b": {"d": 1, "e": 3},
        "f": [{"d": 1, "e": 2}],
        "h": [{"d": 1, "e": 3}, {"d": 2}],
        "i": ["abc", "ghi"],
    }
    resource_client.compare(inputs, outputs)


def test_compare_should_throw_exception(resource_client):
    resource_client._update_schema(SCHEMA_WITH_NESTED_PROPERTIES)
    inputs = {"b": {"d": 1}, "f": [{"d": 1}], "h": [{"d": 1}], "z": 1}

    outputs = {
        "b": {"d": 1, "e": 2},
        "f": [{"d": 1}],
        "h": [{"d": 1}],
    }
    try:
        resource_client.compare(inputs, outputs)
    except AssertionError:
        logging.debug("This test expects Assertion Exception to be thrown")


@pytest.mark.parametrize(
    "inputs,outputs,schema_fragment",
    [
        (
            {"CollectionToCompare": ["item1", "item2", "item3"]},
            {"CollectionToCompare": ["item3", "item2", "item1"]},
            {"properties": {"CollectionToCompare": {"insertionOrder": False}}},
        ),
        (
            {"CollectionToCompare": ["item1", "item2", "item3"]},
            {"CollectionToCompare": ["item1", "item2", "item3"]},
            {"properties": {"CollectionToCompare": {"insertionOrder": True}}},
        ),
        (
            {
                "CollectionToCompare": [
                    "item1",
                    "item2",
                    "item3",
                    {"i": ["item1", "item2"]},
                    [
                        {"j1": {"z": {"l": 10}}, "k3": ["item5", "item4", "item1"]},
                        {"j": {"z": {"l": 10}}, "k": ["item4", "item3", "item2"]},
                    ],
                ]
            },
            {
                "CollectionToCompare": [
                    "item3",
                    "item2",
                    "item1",
                    {"i": ["item2", "item1"]},
                    [
                        {"j": {"k": ["item2", "item3", "item4"], "z": {"l": 10}}},
                        {"j1": {"k3": ["item1", "item5", "item4"], "z": {"l": 10}}},
                    ],
                ]
            },
            {"properties": {"CollectionToCompare": {"insertionOrder": False}}},
        ),
        (
            {
                "Collection": {
                    "PropertyA": {"A": True},
                    "CollectionToCompare": ["item1", "item2", "item3"],
                }
            },
            {
                "Collection": {
                    "PropertyA": {"A": True},
                    "CollectionToCompare": ["item3", "item2", "item1"],
                }
            },
            {
                "definitions": {
                    "PropertyA": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"A": {"type": "boolean"}},
                    },
                    "Collection": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "PropertyA": {"$ref": "#/definitions/PropertyA"},
                            "CollectionToCompare": {
                                "insertionOrder": False,
                                "type": "array",
                                "items": {"type": "string", "minItems": 1},
                            },
                        },
                    },
                },
                "properties": {"Collection": {"$ref": "#/definitions/Collection"}},
            },
        ),
        (
            {
                "Collections": [
                    {
                        "InnerCollection": {
                            "Items": ["item2", "item1"],
                            "IntegerProperty": 10,
                        }
                    }
                ]
            },
            {
                "Collections": [
                    {
                        "InnerCollection": {
                            "Items": ["item1", "item2"],
                            "IntegerProperty": 10,
                        }
                    }
                ]
            },
            {
                "definitions": {
                    "InnerCollection": {
                        "type": "object",
                        "properties": {
                            "Items": {
                                "type": "array",
                                "insertionOrder": False,
                                "items": {"type": "string"},
                            },
                            "IntegerProperty": {"type": "integer"},
                        },
                    },
                    "Collection": {
                        "type": "object",
                        "properties": {
                            "InnerCollection": {"$ref": "#/definitions/InnerCollection"}
                        },
                    },
                },
                "properties": {
                    "Collections": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"$ref": "#/definitions/Collection"},
                    },
                },
            },
        ),
        (
            {
                "OptionConfigurations": [
                    {
                        "OptionSettings": [
                            {"Name": "BACKLOG_QUEUE_LIMIT", "Value": "1024"},
                            {"Name": "CHUNK_SIZE", "Value": "32"},
                        ]
                    }
                ]
            },
            {
                "OptionConfigurations": [
                    {
                        "OptionSettings": [
                            {"Name": "CHUNK_SIZE", "Value": "32"},
                            {"Name": "BACKLOG_QUEUE_LIMIT", "Value": "1024"},
                        ]
                    }
                ]
            },
            {
                "definitions": {
                    "OptionConfiguration": {
                        "type": "object",
                        "properties": {
                            "OptionSettings": {
                                "type": "array",
                                "insertionOrder": False,
                                "items": {"$ref": "#/definitions/OptionSetting"},
                            }
                        },
                    },
                    "OptionSetting": {
                        "type": "object",
                        "properties": {
                            "Name": {"type": "string"},
                            "Value": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "properties": {
                    "OptionConfigurations": {
                        "type": "array",
                        "insertionOrder": False,
                        "items": {"$ref": "#/definitions/OptionConfiguration"},
                    },
                },
            },
        ),
    ],
)
def test_compare_collection(resource_client, inputs, outputs, schema_fragment):
    resource_client._update_schema(schema_fragment)

    resource_client.compare(inputs, outputs)


def test_compare_should_throw_key_error(resource_client):
    resource_client._update_schema(SCHEMA_WITH_NESTED_PROPERTIES)
    inputs = {"b": {"d": 1}, "f": [{"d": 1}], "h": [{"d": 1}]}

    outputs = {"b": {"d": 1, "e": 2}, "f": [{"d": 1, "e": 2}, {"d": 2, "e": 3}]}
    try:
        resource_client.compare(inputs, outputs)
    except AssertionError:
        logging.debug("This test expects Assertion Exception to be thrown")


def test_compare_ordered_list_throws_assertion_exception(resource_client):
    resource_client._update_schema(SCHEMA_WITH_NESTED_PROPERTIES)
    inputs = {"b": {"d": 1}, "f": [{"d": 1}], "h": [{"d": 1}], "i": ["abc", "ghi"]}

    outputs = {
        "b": {"d": 1, "e": 2},
        "f": [{"e": 2}, {"d": 2, "e": 3}],
        "i": ["abc", "ghi", "tt"],
    }
    try:
        resource_client.compare(inputs, outputs)
    except AssertionError:
        logging.debug("This test expects Assertion Exception to be thrown")
