import logging

from cfnlint import lint

from .module_fragment_reader import _get_fragment_file

LOG = logging.getLogger(__name__)


def print_cfn_lint_warnings(fragment_dir):
    lint_warnings = __get_cfn_lint_matches(fragment_dir)
    if not lint_warnings:
        LOG.warning("Module fragment is valid.")
    else:
        LOG.warning(
            "Module fragment might be valid, but there are "
            "warnings from cfn-lint "
            "(https://github.com/aws-cloudformation/cfn-python-lint):"
        )
        for lint_warning in lint_warnings:
            print(
                f"\t{lint_warning.message} (from rule {lint_warning.rule})",
            )


def __get_cfn_lint_matches(fragment_dir):
    filepath = _get_fragment_file(fragment_dir)

    try:
        with open(filepath, encoding="utf-8") as handle:
            template = handle.read()
            matches = lint(template, config={"regions": ["us-east-1"]})

            return matches
    except Exception as e:  # pylint: disable=broad-except
        LOG.error(
            "Skipping cfn-lint validation due to an internal error.\n"
            "Please report this issue to the team (include rpdk.log file)\n"
            "Issue tracker: github.com/aws-cloudformation/cloudformation-cli/issues"
        )
        LOG.error(str(e))
        return []
