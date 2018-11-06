import pytest

from .. import contract_utils


def contract_create_ack(resource_client):
    request, token = resource_client.prepare_request(contract_utils.CREATE)
    events = resource_client.send_async_request(
        request, token, contract_utils.IN_PROGRESS
    )
    assert events[0]["status"] == contract_utils.IN_PROGRESS


def contract_create_create(
    resource_client, test_resource, created_resource
):  # pylint: disable=unused-argument
    if resource_client.get_identifier_property(test_resource, writable=True) is None:
        pytest.skip("No writable identifiers")
    second_create_terminal_event = resource_client.create_resource(test_resource)
    assert second_create_terminal_event["status"] == contract_utils.FAILED
    assert second_create_terminal_event["errorCode"] == contract_utils.ALREADY_EXISTS


def contract_create_noop_update(resource_client, test_resource, created_resource):
    update_terminal_event = resource_client.update_resource(
        created_resource, test_resource
    )
    assert update_terminal_event["status"] == contract_utils.FAILED
    assert update_terminal_event["errorCode"] == contract_utils.NO_OP


def contract_create_update(resource_client, test_updated_resource, created_resource):
    update_terminal_event = resource_client.update_resource(
        created_resource, test_updated_resource
    )
    updated_resource = update_terminal_event["resources"][0]
    assert update_terminal_event["status"] == contract_utils.COMPLETE
    resource_client.compare_requested_model(test_updated_resource, updated_resource)


def contract_create_read(resource_client, created_resource):
    read_response = resource_client.read_resource(created_resource)
    assert read_response["status"] == contract_utils.COMPLETE
    assert read_response["resources"][0] == created_resource


def contract_create_delete(resource_client, created_resource, test_resource):
    resource_client.compare_requested_model(test_resource, created_resource)
