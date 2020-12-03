# fixture and parameter have the same name
# pylint: disable=redefined-outer-name
import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from rpdk.core.cli import EXIT_UNHANDLED_EXCEPTION, main
from rpdk.core.contract.interface import Action
from rpdk.core.exceptions import SysExitRecommendedError
from rpdk.core.project import ARTIFACT_TYPE_MODULE, ARTIFACT_TYPE_RESOURCE, Project
from rpdk.core.test import (
    DEFAULT_ENDPOINT,
    DEFAULT_FUNCTION,
    DEFAULT_REGION,
    _validate_sam_args,
    empty_override,
    get_inputs,
    get_marker_options,
    get_overrides,
    temporary_ini_file,
)

RANDOM_INI = "pytest_SOYPKR.ini"
EMPTY_OVERRIDE = empty_override()
ROLE_ARN = "role_arn"
CREDENTIALS = {
    "AccessKeyId": object(),
    "SecretAccessKey": object(),
    "SessionToken": object(),
}


SCHEMA = {"handlers": {action.lower(): [] for action in Action}}


@pytest.fixture
def base(tmpdir):
    return Path(tmpdir)


@contextmanager
def mock_temporary_ini_file():
    yield RANDOM_INI


def create_input_file(base, create_string, update_string, invalid_string):
    path = base / "inputs"
    os.mkdir(path, mode=0o777)

    path_create = path / "inputs_1_create.json"
    with path_create.open("w", encoding="utf-8") as f:
        f.write(create_string)

    path_update = path / "inputs_1_update.json"
    with path_update.open("w", encoding="utf-8") as f:
        f.write(update_string)

    path_invalid = path / "inputs_1_invalid.json"
    with path_invalid.open("w", encoding="utf-8") as f:
        f.write(invalid_string)


def create_invalid_input_file(base):
    path = base / "inputs"
    os.mkdir(path, mode=0o777)

    path_create = path / "inputs_1_test.json"
    with path_create.open("w", encoding="utf-8") as f:
        f.write('{"a": 1}')


@pytest.mark.parametrize(
    "args_in,pytest_args,plugin_args",
    [
        ([], [], [DEFAULT_FUNCTION, DEFAULT_ENDPOINT, DEFAULT_REGION, "30"]),
        (["--endpoint", "foo"], [], [DEFAULT_FUNCTION, "foo", DEFAULT_REGION, "30"]),
        (
            ["--function-name", "bar", "--enforce-timeout", "60"],
            [],
            ["bar", DEFAULT_ENDPOINT, DEFAULT_REGION, "60"],
        ),
        (
            ["--", "-k", "create"],
            ["-k", "create"],
            [DEFAULT_FUNCTION, DEFAULT_ENDPOINT, DEFAULT_REGION, "30"],
        ),
        (
            ["--region", "us-west-2", "--", "--collect-only"],
            ["--collect-only"],
            [DEFAULT_FUNCTION, DEFAULT_ENDPOINT, "us-west-2", "30"],
        ),
    ],
)
def test_test_command_happy_path(
    base, capsys, args_in, pytest_args, plugin_args
):  # pylint: disable=too-many-locals
    create_input_file(base, '{"a": 1}', '{"a": 2}', '{"b": 1}')
    mock_project = Mock(spec=Project)
    mock_project.schema = SCHEMA
    mock_project.root = base
    mock_project.executable_entrypoint = None
    mock_project.artifact_type = ARTIFACT_TYPE_RESOURCE

    patch_project = patch(
        "rpdk.core.test.Project", autospec=True, return_value=mock_project
    )
    patch_plugin = patch("rpdk.core.test.ContractPlugin", autospec=True)
    patch_client = patch("rpdk.core.test.ResourceClient", autospec=True)
    patch_pytest = patch("rpdk.core.test.pytest.main", autospec=True, return_value=0)
    patch_ini = patch(
        "rpdk.core.test.temporary_ini_file", side_effect=mock_temporary_ini_file
    )
    # fmt: off
    with patch_project, \
            patch_plugin as mock_plugin, \
            patch_client as mock_client, \
            patch_pytest as mock_pytest, \
            patch_ini as mock_ini:
        main(args_in=["test"] + args_in)
    # fmt: on

    mock_project.load.assert_called_once_with()
    function_name, endpoint, region, enforce_timeout = plugin_args
    mock_client.assert_called_once_with(
        function_name,
        endpoint,
        region,
        mock_project.schema,
        EMPTY_OVERRIDE,
        {"CREATE": {"a": 1}, "UPDATE": {"a": 2}, "INVALID": {"b": 1}},
        None,
        enforce_timeout,
        None,
        None,
    )
    mock_plugin.assert_called_once_with(mock_client.return_value)
    mock_ini.assert_called_once_with()
    mock_pytest.assert_called_once_with(
        ["-c", RANDOM_INI, "-m", ""] + pytest_args, plugins=[mock_plugin.return_value]
    )

    _out, err = capsys.readouterr()
    assert not err


def test_test_command_return_code_on_error():
    mock_project = Mock(spec=Project)

    mock_project.root = None
    mock_project.schema = SCHEMA
    mock_project.executable_entrypoint = None
    mock_project.artifact_type = ARTIFACT_TYPE_RESOURCE
    patch_project = patch(
        "rpdk.core.test.Project", autospec=True, return_value=mock_project
    )
    patch_plugin = patch("rpdk.core.test.ContractPlugin", autospec=True)
    patch_client = patch("rpdk.core.test.ResourceClient", autospec=True)
    patch_pytest = patch("rpdk.core.test.pytest.main", autospec=True, return_value=1)
    with patch_project, patch_plugin, patch_client, patch_pytest:
        with pytest.raises(SystemExit) as excinfo:
            main(args_in=["test"])

    assert excinfo.value.code != EXIT_UNHANDLED_EXCEPTION


def test_test_command_module_project_succeeds():
    mock_project = Mock(spec=Project)

    mock_project.artifact_type = ARTIFACT_TYPE_MODULE
    patch_project = patch(
        "rpdk.core.test.Project", autospec=True, return_value=mock_project
    )
    with patch_project:
        main(args_in=["test"])


def test_temporary_ini_file():
    with temporary_ini_file() as path_str:
        assert isinstance(path_str, str)
        path = Path(path_str)
        assert path.name.startswith("pytest_")
        assert path.name.endswith(".ini")

        with path.open("r", encoding="utf-8") as f:
            assert "[pytest]" in f.read()


def test_get_overrides_no_root():
    assert get_overrides(None, DEFAULT_REGION, "", None) == EMPTY_OVERRIDE


def test_get_overrides_file_not_found(base):
    path = base / "overrides.json"
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    assert get_overrides(path, DEFAULT_REGION, "", None) == EMPTY_OVERRIDE


def test_get_overrides_invalid_file(base):
    path = base / "overrides.json"
    path.write_text("{}")
    assert get_overrides(base, DEFAULT_REGION, "", None) == EMPTY_OVERRIDE


def test_get_overrides_empty_overrides(base):
    path = base / "overrides.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(EMPTY_OVERRIDE, f)
    assert get_overrides(base, DEFAULT_REGION, "", None) == EMPTY_OVERRIDE


def test_get_overrides_invalid_pointer_skipped(base):
    overrides = empty_override()
    overrides["CREATE"]["#/foo/bar"] = None

    path = base / "overrides.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(overrides, f)
    assert get_overrides(base, DEFAULT_REGION, "", None) == EMPTY_OVERRIDE


def test_get_overrides_good_path(base):
    overrides = empty_override()
    overrides["CREATE"]["/foo/bar"] = {}

    path = base / "overrides.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(overrides, f)
    assert get_overrides(base, DEFAULT_REGION, "", None) == {
        "CREATE": {("foo", "bar"): {}}
    }


@pytest.mark.parametrize(
    "overrides_string,list_exports_return_value,expected_overrides",
    [
        (
            '{"CREATE": {"/foo/bar": "{{TestInvalidExport}}"}}',
            [{"Exports": [{"Value": "TestValue", "Name": "Test"}]}],
            empty_override(),
        ),
        (
            '{"CREATE": {"/foo/bar": {{TestExport}}}}',
            [{"Exports": [{"Value": 5, "Name": "TestExport"}]}],
            {"CREATE": {("foo", "bar"): 5}},
        ),
        (
            '{"CREATE": {"/foo/bar": "{{TestExport}}"}}',
            [
                {"Exports": [{"Value": "FirstTestValue", "Name": "FirstTestExport"}]},
                {"Exports": [{"Value": "TestValue", "Name": "TestExport"}]},
            ],
            {"CREATE": {("foo", "bar"): "TestValue"}},
        ),
        (
            '{"CREATE": {"/foo/bar": "{{TestExport}}",'
            + ' "/foo/bar2": "{{TestInvalidExport}}"}}',
            [{"Exports": [{"Value": "TestValue", "Name": "TestExport"}]}],
            empty_override(),
        ),
    ],
)
def test_get_overrides_with_jinja(
    base, overrides_string, list_exports_return_value, expected_overrides
):
    mock_sts_client = Mock(spec=["get_session_token"])
    mock_cfn_client = Mock(spec=["get_paginator"])
    mock_paginator = Mock(spec=["paginate"])
    mock_cfn_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = list_exports_return_value
    mock_sts_client.get_session_token.return_value = CREDENTIALS
    patch_sdk = patch("rpdk.core.test.create_sdk_session", autospec=True)

    path = base / "overrides.json"
    with path.open("w", encoding="utf-8") as f:
        f.write(overrides_string)
    with patch_sdk as mock_sdk:
        mock_sdk.return_value.region_name = "us-east-1"
        mock_sdk.return_value.client.side_effect = [
            mock_sts_client,
            mock_cfn_client,
            Mock(),
        ]
        result = get_overrides(base, DEFAULT_REGION, None, None)

    assert result == expected_overrides


@pytest.mark.parametrize(
    "schema,expected_marker_keywords",
    [
        (SCHEMA, ""),
        (
            {"handlers": {"create": [], "read": [], "update": [], "delete": []}},
            ("not list",),
        ),
        (
            {"handlers": {"create": []}},
            ("not read", "not update", "not delete", "not list", " and "),
        ),
    ],
)
def test_get_marker_options(schema, expected_marker_keywords):
    marker_options = get_marker_options(schema)
    assert all(keyword in marker_options for keyword in expected_marker_keywords)


@pytest.mark.parametrize(
    "create_string,update_string,invalid_string,"
    "list_exports_return_value,expected_inputs",
    [
        (
            '{"Name": "TestName"}',
            '{"Name": "TestNameNew"}',
            '{"Name": "TestNameNew"}',
            [{"Exports": [{"Value": "TestValue", "Name": "Test"}]}],
            {
                "CREATE": {"Name": "TestName"},
                "UPDATE": {"Name": "TestNameNew"},
                "INVALID": {"Name": "TestNameNew"},
            },
        )
    ],
)
# pylint: disable=R0913
# pylint: disable=R0914
def test_with_inputs(
    base,
    create_string,
    update_string,
    invalid_string,
    list_exports_return_value,
    expected_inputs,
):
    mock_sts_client = Mock(spec=["get_session_token"])
    mock_sts_client.get_session_token.return_value = CREDENTIALS
    mock_cfn_client = Mock(spec=["get_paginator"])
    mock_paginator = Mock(spec=["paginate"])
    mock_cfn_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = list_exports_return_value
    patch_sdk = patch("rpdk.core.test.create_sdk_session", autospec=True)

    create_input_file(base, create_string, update_string, invalid_string)
    with patch_sdk as mock_sdk:
        mock_sdk.return_value.region_name = "us-east-1"
        mock_sdk.return_value.client.side_effect = [
            mock_sts_client,
            mock_cfn_client,
            Mock(),
        ]
        result = get_inputs(base, DEFAULT_REGION, None, 1, None)

    assert result == expected_inputs


def test_with_inputs_invalid(base):
    mock_sts_client = Mock(spec=["get_session_token"])
    mock_sts_client.get_session_token.return_value = CREDENTIALS
    mock_cfn_client = Mock(spec=["get_paginator"])
    mock_paginator = Mock(spec=["paginate"])
    mock_cfn_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = (
        '[{"Exports": [{"Value": "TestValue", "Name": "Test"}]}]'
    )
    patch_sdk = patch("rpdk.core.test.create_sdk_session", autospec=True)

    create_invalid_input_file(base)
    with patch_sdk as mock_sdk:
        mock_sdk.return_value.region_name = "us-east-1"
        mock_sdk.return_value.client.side_effect = [
            mock_sts_client,
            mock_cfn_client,
            Mock(),
        ]
        result = get_inputs(base, DEFAULT_REGION, None, 1, None)

    assert not result


def test_get_input_invalid_root():
    assert not get_inputs("", DEFAULT_REGION, "", 1, None)


def test_get_input_input_folder_does_not_exist(base):
    assert not get_inputs(base, DEFAULT_REGION, "", 1, None)


def test_get_input_file_not_found(base):
    path = base / "inputs"
    os.mkdir(path, mode=0o777)
    assert not get_inputs(base, DEFAULT_REGION, "", 1, None)


def test_use_both_sam_and_docker_arguments():
    args = Mock(spec_set=["docker_image", "endpoint"])
    args.docker_image = "image"
    args.endpoint = "endpoint"
    try:
        _validate_sam_args(args)
    except SysExitRecommendedError as e:
        assert (
            "Cannot specify both --docker-image and --endpoint or --function-name"
            in str(e)
        )
