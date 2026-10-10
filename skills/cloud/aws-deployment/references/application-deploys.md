# Deploying an Application

Reference shapes for common workloads. Adjust names, regions, and ARNs; verify
every field against the service's current API documentation.

## ECS on Fargate, private subnets, with an ALB

Task definition (JSON):

```json
{
  "family": "acme-api",
  "requiresCompatibilities": ["FARGATE"],
  "networkMode": "awsvpc",
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "arn:aws:iam::123456789012:role/acme-api-exec",
  "taskRoleArn": "arn:aws:iam::123456789012:role/acme-api-task",
  "containerDefinitions": [
    {
      "name": "api",
      "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/acme-api:1.4.2",
      "essential": true,
      "portMappings": [
        { "containerPort": 8080, "protocol": "tcp", "appProtocol": "http" }
      ],
      "secrets": [
        { "name": "DATABASE_URL", "valueFrom": "acme-prod-db" },
        { "name": "SESSION_SECRET", "valueFrom": "acme-prod-session" }
      ],
      "environment": [
        { "name": "NODE_ENV", "value": "production" },
        { "name": "LOG_LEVEL", "value": "info" }
      ],
      "healthCheck": {
        "command": ["CMD-SHELL", "node -e \"fetch('http://127.0.0.1:8080/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 20
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/acme-api",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "api",
          "awslogs-create-group": "false"
        }
      },
      "readonlyRootFilesystem": true,
      "user": "10001:10001"
    }
  ]
}
```

Note the two distinct roles:

- **Execution role**: what the ECS agent needs to pull images, write logs, and fetch
  secrets on your behalf.
- **Task role**: what your application code is allowed to do with the AWS SDK.
  Keeping them separate makes the application permissions small and auditable.

Service:

```bash
aws ecs create-service \
  --cluster acme --service-name acme-api \
  --task-definition acme-api:42 \
  --desired-count 4 \
  --launch-type FARGATE \
  --deployment-configuration 'deploymentCircuitBreaker={"enabled":true,"rollback":true},minimumHealthyPercent=100,maximumPercent=200' \
  --network-configuration 'awsvpcConfiguration={"subnets":["subnet-priv-a","subnet-priv-b"],"securityGroups":["sg-app"],"assignPublicIp":"DISABLED"}' \
  --load-balancers 'loadBalancerName=acme-alb,targetGroupArn=arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/acme-api/abc123,containerName=api,containerPort=8080' \
  --health-check-grace-period-seconds 60 \
  --enable-execute-command
```

`awsvpc` plus `assignPublicIp: DISABLED` puts tasks in private subnets. They reach
the internet through the NAT gateway and AWS APIs through VPC endpoints. The
deployment circuit breaker with rollback turns a stuck rollout into an automatic
rollback, which is the single most valuable ECS setting for availability.

## Lambda with least-privilege role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadConfigFromParameterStore",
      "Effect": "Allow",
      "Action": "ssm:GetParameters",
      "Resource": "arn:aws:ssm:us-east-1:123456789012:parameter/acme/prod/*"
    },
    {
      "Sid": "WriteOwnMetric",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*",
      "Condition": {
        "StringEquals": { "cloudwatch:namespace": "Acme/Api" }
      }
    }
  ]
}
```

The `cloudwatch:PutMetricData` deny-all-plus-condition pattern is the standard way to
scope an API that does not support resource-level permissions. `"Resource": "*"` is
acceptable here because the condition makes it meaningfully narrow; document why.

```bash
aws lambda create-function \
  --function-name acme-webhook-handler \
  --runtime nodejs22.x \
  --handler index.handler \
  --role arn:aws:iam::123456789012:role/acme-lambda-exec \
  --zip-file fileb://function.zip \
  --memory-size 512 \
  --timeout 30 \
  --reserved-concurrent-executions 20 \
  --environment Variables='{NODE_ENV=production}' \
  --vpc-config SubnetIds=subnet-priv-a,subnet-priv-b,SecurityGroupIds=sg-lambda \
  --dead-letter-config TargetArn=arn:aws:sqs:us-east-1:123456789012:acme-dlq

aws lambda put-function-concurrency \
  --function-name acme-webhook-handler --reserved-concurrent-executions 20

aws lambda put-function-code-signing-config --code-signing-arn arn:aws:lambda:...
```

VPC-attached Lambda needs either interface endpoints in the VPC or a NAT gateway. It
also loses some AWS API access automatically, which surprises people: without an
endpoint or NAT path, an attached function cannot reach Secrets Manager or STS.

## Blue/green with two target groups

```bash
# 1. Deploy the new version to a second target group
aws ecs update-service --cluster acme --service acme-api-blue \
  --task-definition acme-api:43 --force-new-deployment

# 2. Smoke test against the new target group
ALB_DNS=$(aws elbv2 describe-load-balancers --names acme-alb \
  --query 'LoadBalancers[0].DNSName' --output text)
curl -fsS "https://${ALB_DNS}/healthz" || exit 1

# 3. Shift traffic with a weighted listener rule
aws elbv2 modify-rule --rule-arn arn:aws:elasticloadbalancing:...:listener-rule/... \
  --actions '[
    {"Type":"forward","TargetGroupArn":"...blue...","Order":5000},
    {"Type":"forward","TargetGroupArn":"...green...","Order":10000,"Weight":10000}
  ]'

# 4. Roll back
aws elbv2 modify-rule --rule-arn arn:aws:elasticloadbalancing:... \
  --actions '[{"Type":"forward","TargetGroupArn":"...green...","Order":5000}]'
```

Blue/green doubles running capacity during the cutover and requires the two versions
to be compatible with the same database schema. Roll forward with an expand-and-
contract migration if they are not.

## CloudFront in front of an ALB or S3

```
CloudFront (edge)
  ├── behavior /*        -> ALB origin        (dynamic, cache policy minimal)
  └── behavior /static/* -> S3 origin         (long TTL, versioning)
```

```bash
aws cloudfront create-distribution --origin-config file://origin-config.json \
  --default-cache-behavior file://cache-behavior.json \
  --viewer-certificate AcmCertificateArn=arn:aws:acm:us-east-1:..., \
    SslSupportMethod=sni-only,MinimumProtocolVersion=TLSv1.2_2021
```

Keep dynamic content cached briefly at the edge only if your application already sets
correct `Cache-Control` headers. Do not paper over a missing cache policy with a long
edge TTL; that serves stale APIs and hides bugs.

## Smoke test after deploy

```bash
#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${1:?usage: smoke.sh https://host}"
TIMEOUT_SECONDS="${2:-120}"
START=$(date +%s)

until curl -fsS --max-time 5 "$ENDPOINT/healthz" >/dev/null; do
  if [ $(( $(date +%s) - START )) -ge "$TIMEOUT_SECONDS" ]; then
    echo "health check never succeeded for $ENDPOINT"
    exit 1
  fi
  sleep 5
done

# Assert the deployed version, not just that something answered
VERSION=$(curl -fsS "$ENDPOINT/version")
case "$VERSION" in
  "$EXPECTED_VERSION"*) echo "deployed $VERSION" ;;
  *) echo "expected $EXPECTED_VERSION, got $VERSION" >&2; exit 1 ;;
esac

# Assert the error rate is not elevated
curl -fsS "$ENDPOINT/metrics" | grep -q 'errors_5xx 0' \
  || { echo "5xx errors above zero" >&2; exit 1; }
```

Version verification matters: "the health endpoint returned 200" proves a load
balancer has a healthy target, not that *your* build is on it.

## Rolling back

```bash
# ECS
aws ecs update-service --cluster acme --service acme-api \
  --task-definition acme-api:41 --force-new-deployment

# Lambda: publish a new version from the previous function code
aws lambda get-function --function-name acme-webhook-handler --qualifier '$PREVIOUS'
aws lambda update-function-code --function-name acme-webhook-handler \
  --zip-file fileb://previous.zip

# RDS: automated failover to the standby
aws rds reboot-db-instance --db-instance-identifier acme-prod --force-failover

# ALB target group: shift traffic back
aws elbv2 modify-target-group-attributes ...
```

Know the rollback path for every component *before* deploying it, and know its cost.
A rollback that loses writes because of the wrong database promotion is not a
rollback.