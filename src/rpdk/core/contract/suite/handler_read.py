# fixture and parameter have the same name
# pylint: disable=redefined-outer-name

import pytest

# WARNING: contract tests should use fully qualified imports to avoid issues
# when being loaded by pytest
from rpdk.core.contract.suite.contract_asserts import skip_not_writable_identifier
from rpdk.core.contract.suite.handler_commons import test_read_failure_not_found


@pytest.mark.read
@skip_not_writable_identifier
def contract_read_without_create(resource_client):
    model = resource_client.generate_create_example()
    test_read_failure_not_found(resource_client, model)
