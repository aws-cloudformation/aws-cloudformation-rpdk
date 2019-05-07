# fixture and parameter have the same name
# pylint: disable=redefined-outer-name,useless-super-delegation,protected-access
import json
import zipfile
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

from rpdk.core.exceptions import InternalError, InvalidProjectError, SpecValidationError
from rpdk.core.plugin_base import LanguagePlugin
from rpdk.core.project import SCHEMA_UPLOAD_FILENAME, Project
from rpdk.core.upload import Uploader

from .utils import CONTENTS_UTF8, UnclosingBytesIO

LANGUAGE = "BQHDBC"
TYPE_NAME = "AWS::Color::Red"
REGION = "us-east-1"
ENDPOINT = "cloudformation.beta.com"


@pytest.fixture
def project():
    return Project()


@contextmanager
def patch_settings(project, data):
    with patch.object(project, "settings_path", autospec=True) as mock_path:
        mock_path.open.return_value.__enter__.return_value = StringIO(data)
        yield mock_path.open


def test_load_settings_invalid_json(project):
    with patch_settings(project, "") as mock_open:
        with pytest.raises(InvalidProjectError):
            project.load_settings()
    mock_open.assert_called_once_with("r", encoding="utf-8")


def test_load_settings_invalid_settings(project):
    with patch_settings(project, "{}") as mock_open:
        with pytest.raises(InvalidProjectError):
            project.load_settings()
    mock_open.assert_called_once_with("r", encoding="utf-8")


def test_load_settings_valid_json(project):
    plugin = object()
    data = json.dumps({"typeName": TYPE_NAME, "language": LANGUAGE})
    patch_load = patch(
        "rpdk.core.project.load_plugin", autospec=True, return_value=plugin
    )

    with patch_settings(project, data) as mock_open, patch_load as mock_load:
        project.load_settings()

    mock_open.assert_called_once_with("r", encoding="utf-8")
    mock_load.assert_called_once_with(LANGUAGE)
    assert project.type_info == ("AWS", "Color", "Red")
    assert project.type_name == TYPE_NAME
    assert project._plugin is plugin
    assert project.settings == {}


def test_load_schema_settings_not_loaded(project):
    with pytest.raises(InternalError):
        project.load_schema()


def test_load_schema_example(tmpdir):
    project = Project(root=tmpdir)
    project.type_name = "AWS::Color::Blue"
    project._write_example_schema()
    project.load_schema()


def test_overwrite():
    mock_path = MagicMock(spec=Path)
    Project.overwrite(mock_path, LANGUAGE)

    mock_path.open.assert_called_once_with("w", encoding="utf-8")
    mock_f = mock_path.open.return_value.__enter__.return_value
    mock_f.write.assert_called_once_with(LANGUAGE)


def test_safewrite_overwrite(project):
    path = object()
    contents = object()

    patch_attr = patch.object(project, "_overwrite", True)
    patch_meth = patch.object(project, "overwrite", autospec=True)
    with patch_attr, patch_meth as mock_overwrite:
        project.safewrite(path, contents)

    mock_overwrite.assert_called_once_with(path, contents)


def test_safewrite_doesnt_exist(project, tmpdir):
    path = Path(tmpdir.join("test")).resolve()

    with patch.object(project, "_overwrite", False):
        project.safewrite(path, CONTENTS_UTF8)

    with path.open("r", encoding="utf-8") as f:
        assert f.read() == CONTENTS_UTF8


def test_safewrite_exists(project, tmpdir, caplog):
    path = Path(tmpdir.join("test")).resolve()

    with path.open("w", encoding="utf-8") as f:
        f.write(CONTENTS_UTF8)

    with patch.object(project, "_overwrite", False):
        project.safewrite(path, CONTENTS_UTF8)

    last_record = caplog.records[-1]
    assert last_record.levelname == "WARNING"
    assert str(path) in last_record.message


def test_generate(project):
    mock_plugin = MagicMock(spec=["generate"])
    with patch.object(project, "_plugin", mock_plugin):
        project.generate()
    mock_plugin.generate.assert_called_once_with(project)


def test_init(tmpdir):
    type_name = "AWS::Color::Red"

    mock_plugin = MagicMock(spec=["init"])
    patch_load_plugin = patch(
        "rpdk.core.project.load_plugin", autospec=True, return_value=mock_plugin
    )

    project = Project(root=tmpdir)
    with patch_load_plugin as mock_load_plugin:
        project.init(type_name, LANGUAGE)

    mock_load_plugin.assert_called_once_with(LANGUAGE)
    mock_plugin.init.assert_called_once_with(project)

    assert project.type_info == ("AWS", "Color", "Red")
    assert project.type_name == type_name
    assert project._plugin is mock_plugin
    assert project.settings == {}

    with project.settings_path.open("r", encoding="utf-8") as f:
        assert json.load(f)

    with project.schema_path.open("r", encoding="utf-8") as f:
        assert json.load(f)


def test_load_invalid_schema(project):
    patch_settings = patch.object(project, "load_settings")
    patch_schema = patch.object(
        project, "load_schema", side_effect=SpecValidationError("")
    )
    with patch_settings as mock_settings, patch_schema as mock_schema, pytest.raises(
        InvalidProjectError
    ) as excinfo:
        project.load()

    mock_settings.assert_called_once_with()
    mock_schema.assert_called_once_with()

    assert "invalid" in str(excinfo.value)


def test_schema_not_found(project):
    patch_settings = patch.object(project, "load_settings")
    patch_schema = patch.object(project, "load_schema", side_effect=FileNotFoundError)
    with patch_settings as mock_settings, patch_schema as mock_schema, pytest.raises(
        InvalidProjectError
    ) as excinfo:
        project.load()

    mock_settings.assert_called_once_with()
    mock_schema.assert_called_once_with()

    assert "not found" in str(excinfo.value)


def test_settings_not_found(project):
    patch_settings = patch.object(
        project, "load_settings", side_effect=FileNotFoundError
    )
    patch_schema = patch.object(project, "load_schema")

    with patch_settings as mock_settings, patch_schema as mock_schema, pytest.raises(
        InvalidProjectError
    ) as excinfo:
        project.load()

    mock_settings.assert_called_once_with()
    mock_schema.assert_not_called()

    assert "not found" in str(excinfo.value)
    assert "init" in str(excinfo.value)


def test_submit_dry_run(project, tmpdir):
    project.type_name = TYPE_NAME
    project.root = Path(tmpdir).resolve()
    zip_path = project.root / "test.zip"

    with project.schema_path.open("w", encoding="utf-8") as f:
        f.write(CONTENTS_UTF8)

    patch_plugin = patch.object(project, "_plugin", spec=LanguagePlugin)
    patch_upload = patch.object(project, "_upload", autospec=True)
    patch_path = patch("rpdk.core.project.Path", return_value=zip_path)
    patch_temp = patch("rpdk.core.project.TemporaryFile", autospec=True)

    # fmt: off
    # these context managers can't be wrapped by black, but it removes the \
    with patch_plugin as mock_plugin, patch_path as mock_path, \
            patch_temp as mock_temp, patch_upload as mock_upload:
        project.submit(True, endpoint_url=ENDPOINT, region_name=REGION)
    # fmt: on

    mock_temp.assert_not_called()
    mock_path.assert_called_once_with("{}.zip".format(project.hypenated_name))
    mock_plugin.package.assert_called_once_with(project, ANY)
    mock_upload.assert_not_called()

    with zipfile.ZipFile(zip_path, mode="r") as zip_file:
        assert zip_file.namelist() == [SCHEMA_UPLOAD_FILENAME]
        schema_contents = zip_file.read(SCHEMA_UPLOAD_FILENAME).decode("utf-8")
        assert schema_contents == CONTENTS_UTF8
        # https://docs.python.org/3/library/zipfile.html#zipfile.ZipFile.testzip
        assert zip_file.testzip() is None


def test_submit_live_run(project, tmpdir):
    project.type_name = TYPE_NAME
    project.root = Path(tmpdir).resolve()

    with project.schema_path.open("w", encoding="utf-8") as f:
        f.write(CONTENTS_UTF8)

    temp_file = UnclosingBytesIO()

    patch_plugin = patch.object(project, "_plugin", spec=LanguagePlugin)
    patch_upload = patch.object(project, "_upload", autospec=True)
    patch_path = patch("rpdk.core.project.Path", autospec=True)
    patch_temp = patch("rpdk.core.project.TemporaryFile", return_value=temp_file)

    # fmt: off
    # these context managers can't be wrapped by black, but it removes the \
    with patch_plugin as mock_plugin, patch_path as mock_path, \
            patch_temp as mock_temp, patch_upload as mock_upload:
        project.submit(False, endpoint_url=ENDPOINT, region_name=REGION)
    # fmt: on

    mock_path.assert_not_called()
    mock_temp.assert_called_once_with("w+b")
    mock_plugin.package.assert_called_once_with(project, ANY)

    # zip file construction is tested by the dry-run test

    assert temp_file.tell() == 0  # file was rewound before upload
    mock_upload.assert_called_once_with(
        temp_file, region_name=REGION, endpoint_url=ENDPOINT
    )

    assert temp_file._was_closed
    temp_file._close()


def test__upload(project):
    project.type_name = TYPE_NAME

    mock_cfn_client = MagicMock(spec=["register_resource_type"])
    s3_client = object()
    fileobj = object()

    patch_sdk = patch("rpdk.core.project.create_sdk_session", autospec=True)
    patch_uploader = patch.object(Uploader, "upload", return_value="url")

    with patch_sdk as mock_sdk, patch_uploader as mock_upload_method:
        mock_session = mock_sdk.return_value
        mock_session.client.side_effect = [mock_cfn_client, s3_client]
        project._upload(fileobj, endpoint_url=None, region_name=None)

    mock_sdk.assert_called_once_with()
    mock_upload_method.assert_called_once_with(project.hypenated_name, fileobj)
    mock_cfn_client.register_resource_type.assert_called_once_with(
        SchemaHandlerPackage="url", TypeName=project.type_name
    )
