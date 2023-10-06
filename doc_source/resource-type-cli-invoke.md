# invoke<a name="resource-type-cli-invoke"></a>

## Description<a name="resource-type-cli-invoke-description"></a>

Performs contract tests on the specified handler of a resource type\.

## Synopsis<a name="resource-type-cli-invoke-synopsis"></a>

```
  cfn invoke
[--endpoint <value>]
[--function-name <value>]
[--profile <value>]
[--region <value>]
[--max-reinvoke <value>]
action
request
```

## Options<a name="resource-type-cli-invoke-options"></a>

`--endpoint <value>`

The endpoint at which the type can be invoked\. Alternately, you can also specify an actual Lambda endpoint and function name in your AWS account\.

Default: `http://127.0.0.1.3001`

`--function-name <value>`

The logical Lambda function name in the SAM template\. Alternately, you can also specify an actual Lambda endpoint and function name in your AWS account\.

Default: `TypeFunction`

`--profile <value>`

The AWS profile to use\. If no profile is specified, the client applies credentials specified in the [Boto3 credentials chain](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/configuration.html)\.

`--region <value>`

The region to configure the client to interact with\.

Default: `us-east-1`

`--max-reinvoke <value>`

Maximum number of IN\_PROGRESS re\-invocations allowed before exiting\. If not specified, will continue to re\- invoke until terminal status is reached\.

`action`

Which single handler to invoke\.

Values: `CREATE` \| `READ` \| `UPDATE` \| `DELETE` \| `LIST`

`request`

File path to a JSON file containing the request with which to invoke the function\.

## Output<a name="resource-type-cli-invoke-output"></a>
