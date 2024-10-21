import logging
import os

import yaml
from cfn_tools import load_yaml

from rpdk.core.exceptions import FragmentValidationError

LOG = logging.getLogger(__name__)
ALLOWED_EXTENSIONS = {".json", ".yaml", ".yml", ".template"}


def read_raw_fragments(fragment_dir):
    return _load_fragment(_get_fragment_file(fragment_dir))


def _load_fragment(fragment_file):
    try:
        with open(fragment_file, "r", encoding="utf-8") as f:
            return load_yaml(__first_pass_syntax_check(f.read()))
    except (yaml.parser.ParserError, yaml.scanner.ScannerError) as e:
        raise FragmentValidationError(
            f"Fragment file '{fragment_file}' is invalid: {str(e)}"
        ) from e


def _get_fragment_file(fragment_dir):
    all_fragment_files = []
    for root, _directories, files in os.walk(fragment_dir):
        for f in files:
            ext = os.path.splitext(f)[-1].lower()
            if ext in ALLOWED_EXTENSIONS:
                all_fragment_files.append(os.path.join(root, f))
    if len(all_fragment_files) == 0:
        raise FragmentValidationError(
            "No module fragment files found in the fragments folder ending in one of"
            f" {ALLOWED_EXTENSIONS}"
        )
    if len(all_fragment_files) > 1:
        raise FragmentValidationError(
            "A Module can only consist of a single template file, but there are "
            + str(len(all_fragment_files))
            + ": "
            + str(all_fragment_files)
        )
    return all_fragment_files[0]


def __first_pass_syntax_check(template):
    if "Fn::ImportValue" in template or "!ImportValue" in template:
        raise FragmentValidationError(
            "Template fragment can't contain any Fn::ImportValue."
        )
    return template
