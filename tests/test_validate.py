from unittest.mock import Mock, patch

from rpdk.cli import main
from rpdk.project import Project


def test_validate_command_valid_schema(capsys):
    mock_project = Mock(spec=Project)

    with patch("rpdk.validate.Project", autospec=True, return_value=mock_project):
        main(args_in=["validate"])

    mock_project.load_settings.assert_called_once_with()
    mock_project.load_schema.assert_called_once_with()

    out, err = capsys.readouterr()
    assert not err
    assert "failed" not in out
