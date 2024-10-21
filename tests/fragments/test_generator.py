import os
from pathlib import Path
from shutil import copyfile

import pytest

from rpdk.core.data_loaders import make_validator, resource_json
from rpdk.core.exceptions import FragmentValidationError
from rpdk.core.fragment.generator import TemplateFragment
from tests.utils import CONTENTS_UTF8

type_name = "AWS::ORG::MYTYPE::MODULE"
TIMEOUT_IN_SECONDS = 10

directory = os.path.dirname(__file__)


@pytest.fixture
def template_fragment(tmpdir):
    return TemplateFragment(type_name, tmpdir)


def test_schema_generator(template_fragment, tmpdir):
    schema = __generate_schema("sample.json", template_fragment)
    assert os.path.exists(tmpdir.join("schema.json"))

    assert len(schema) == 4
    assert len(schema["properties"]) == 2
    assert "Parameters" in schema["properties"]
    assert "Resources" in schema["properties"]
    assert "S3Bucket" in schema["properties"]["Resources"]["properties"]
    assert (
        len(schema["properties"]["Resources"]["properties"]["S3Bucket"]["properties"])
        == 2
    )
    assert (
        schema["properties"]["Resources"]["properties"]["S3Bucket"]["properties"][
            "Properties"
        ]["type"]
        == "object"
    )
    __validate_against_meta_schema(schema)


def test_schema_generator_with_complex_real_module(template_fragment, tmpdir):
    schema = __generate_schema("OpsWorks.json", template_fragment)
    schema_file = tmpdir.join("schema.json")
    assert os.path.exists(schema_file)
    generated_schema = __read_file(schema_file)
    expected_schema = __read_file(
        os.path.join(directory, "../data/sample_fragments/OpsWorksSchema.json")
    )
    assert generated_schema == expected_schema
    __validate_against_meta_schema(schema)


def __read_file(fragment_file):
    with open(fragment_file, "r", encoding="utf-8") as f:
        return f.read()


def test_schema_generation_param_without_description(template_fragment):
    schema = __generate_schema("paramWithoutDescription.yaml", template_fragment)

    assert len(schema) == 4
    assert len(schema["properties"]) == 2
    assert "Parameters" in schema["properties"]
    assert (
        "Description"
        not in schema["properties"]["Parameters"]["properties"]["anInput"]["required"]
    )
    __validate_against_meta_schema(schema)


def test_schema_generation_param_type_aws_specific(template_fragment):
    schema = __generate_schema("aws-specific-parameter.json", template_fragment)

    assert len(schema) == 4
    assert len(schema["properties"]) == 2
    assert (
        schema["properties"]["Parameters"]["properties"]["VpcId"]["properties"]["Type"][
            "type"
        ]
        == "string"
    )
    __validate_against_meta_schema(schema)


def test_template_fragments_without_parameter_section(template_fragment):
    schema = __generate_schema(
        "template_without_parameter_section.json", template_fragment
    )

    assert len(schema) == 4
    assert schema["properties"] is not None
    assert schema["properties"]["Resources"] is not None
    __validate_against_meta_schema(schema)


def test_template_fragments_without_parameter_section_is_valid(template_fragment):
    __assert_validation_throws_no_error(
        "template_without_parameter_section.json", template_fragment
    )


def test_template_fragments_with_date_in_version(template_fragment):
    __assert_validation_throws_no_error(
        "template_with_date_in_version.yaml", template_fragment
    )


def test_template_fragments_without_description(template_fragment):
    schema = __generate_schema("template_without_description.json", template_fragment)

    assert len(schema) == 4
    assert schema["properties"] is not None
    assert schema["description"] is not None
    assert schema["properties"].get("Resources") is not None
    assert schema["properties"].get("Parameters") is not None
    __validate_against_meta_schema(schema)


def test_template_fragment_with_empty_description(template_fragment):
    schema = __generate_schema(
        "template_with_empty_description.json", template_fragment
    )

    assert len(schema) == 4
    assert schema["properties"] is not None
    assert schema["description"] is not None
    assert schema["properties"].get("Resources") is not None
    assert schema["properties"].get("Parameters") is not None
    __validate_against_meta_schema(schema)


def __generate_schema(fragment_file_name, template_fragment):
    create_fragments_folder_with_template(fragment_file_name, template_fragment)
    schema = template_fragment.generate_schema()
    return schema


def test_resolved_generated_schema_is_valid_against_metaschema(
    template_fragment, tmpdir
):
    schema = __generate_schema("secureS3_resolved.json", template_fragment)
    __validate_against_meta_schema(schema)
    assert os.path.exists(tmpdir.join("schema.json"))


def __validate_against_meta_schema(schema):
    __make_resource_validator().validate(schema)


def test_generate_sample_fragment(template_fragment, tmpdir):
    sample_fragment_path = tmpdir.join("fragments").join("sample.json")
    assert not os.path.exists(sample_fragment_path)
    template_fragment.generate_sample_fragment()
    assert os.path.exists(sample_fragment_path)


def test_generate_sample_fragment_succeeds_even_if_fragment_folder_exists(
    template_fragment, tmpdir
):
    sample_fragment_path = tmpdir.join("fragments").join("sample.json")
    os.mkdir(tmpdir.join("fragments"))
    assert not os.path.exists(sample_fragment_path)
    template_fragment.generate_sample_fragment()
    assert os.path.exists(sample_fragment_path)


def test_fragments_are_loaded_yaml_short(template_fragment):
    __assert_validation_throws_no_error("ec2_short.yaml", template_fragment)


def test_template_fragments_are_valid(template_fragment):
    __assert_validation_throws_no_error("sample.json", template_fragment)


def test_template_fragments_import_value(template_fragment):
    __assert_throws_validation_error(
        "import_value.json", template_fragment, "can't contain any Fn::ImportValue"
    )


def test_template_fragments_import_value_short(template_fragment):
    __assert_throws_validation_error(
        "import_short.yaml", template_fragment, "can't contain any Fn::ImportValue"
    )


def test_template_fragments_include_resource_level(template_fragment):
    __assert_throws_validation_error(
        "include.json", template_fragment, "can't use AWS::Include"
    )


def test_template_fragments_include_top_level(template_fragment):
    __assert_throws_validation_error(
        "top_level_include.json", template_fragment, "can't contain any transform"
    )


def test_template_fragments_invalid_transform(template_fragment):
    __assert_throws_validation_error(
        "invalid_transform.json",
        template_fragment,
        "Resource 'Fn::Transform' is invalid",
    )


def test_template_fragments_macros(template_fragment):
    __assert_throws_validation_error(
        "macros.yaml", template_fragment, "can't contain any macro"
    )


def test_template_fragments_nested_stack(template_fragment):
    __assert_throws_validation_error(
        "nested_stack.json", template_fragment, "can't contain nested stack"
    )


def test_template_fragments_transform(template_fragment):
    __assert_throws_validation_error(
        "transform.json", template_fragment, "can't contain transform section"
    )


def test_template_fragments_transform_section(template_fragment):
    __assert_throws_validation_error(
        "transform_section.json", template_fragment, "can't contain transform section"
    )


def test_template_fragments_with_json_syntax_error(template_fragment):
    __assert_throws_validation_error(
        "syntax_error.json", template_fragment, "line 15, column 24"
    )


def test_template_fragments_exports(template_fragment):
    __assert_throws_validation_error(
        "exports.json", template_fragment, "cannot contain any Export"
    )


def test_template_fragments_output_without_export_is_valid(template_fragment):
    __assert_validation_throws_no_error("output.json", template_fragment)


def test_template_fragments_with_sub_is_valid(template_fragment):
    __assert_validation_throws_no_error("ec2withsub.yaml", template_fragment)


def test_template_folder_with_multiple_fragment_files():
    template_fragment = TemplateFragment(
        type_name,
        os.path.join(directory, "../data/sample_fragments/test_multiple_files"),
    )
    with pytest.raises(FragmentValidationError) as validation_error:
        template_fragment.validate_fragments()
    assert "can only consist of a single template file" in str(validation_error.value)


def test_merge_fragments_ignores_unrelated_files():
    template_fragment = TemplateFragment(
        type_name, os.path.join(directory, "../data/sample_fragments/")
    )
    template_fragment.validate_fragments()


def __assert_validation_throws_no_error(template_file_name, template_fragment):
    create_fragments_folder_with_template(template_file_name, template_fragment)
    template_fragment.validate_fragments()


def create_fragments_folder_with_template(template_file_name, template_fragment):
    os.mkdir(template_fragment.fragment_dir)
    copyfile(
        os.path.join(directory, "../data/sample_fragments/" + template_file_name),
        os.path.join(template_fragment.fragment_dir, template_file_name),
    )


def __assert_throws_validation_error(
    template_file_name, template_fragment, expected_error_message_fragment
):
    create_fragments_folder_with_template(template_file_name, template_fragment)
    with pytest.raises(FragmentValidationError) as validation_error:
        template_fragment.validate_fragments()
    assert expected_error_message_fragment in str(validation_error.value)


def test_overwrite_doesnt_exist(template_fragment, tmpdir):
    path = Path(tmpdir.join("test")).resolve()

    template_fragment._overwrite(path, CONTENTS_UTF8)

    with path.open("r", encoding="utf-8") as f:
        assert f.read() == CONTENTS_UTF8


def __make_resource_validator(base_uri=None, timeout=TIMEOUT_IN_SECONDS):
    schema = resource_json(
        __name__,
        "../../src/rpdk/core/data/schema/provider.definition.schema.modules.v1.json",
    )
    return make_validator(schema)
