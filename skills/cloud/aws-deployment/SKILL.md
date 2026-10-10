---
name: AWS Deployment
description: Covers AWS application deployment including IAM policies and least privilege, VPC and networking basics, choosing between EC2, ECS, and Lambda, S3 storage classes and lifecycle, RDS and Aurora, load balancers, Secrets Manager, CloudWatch, cost awareness, and Well-Architected review dimensions. Use when the user asks about deploying an application to AWS, writing IAM policies or roles, designing a VPC, choosing a compute service, configuring S3 or RDS, setting up load balancing, or reviewing an AWS architecture.
metadata:
  opencode/autoinvoke: true
---

# AWS Deployment

AWS is organized into three layers you will interact with constantly: **identity**
(who can do what), **containment** (where it runs, in which account and region), and
**services** (the primitives). Most production incidents on AWS trace back to a gap in
the first two.

The service catalog changes continuously. Read this as a decision framework, and check
current service availability, regional coverage, quotas, and pricing in the linked
official documentation rather than trusting numbers from memory.

## When to use this

- Deploying a web service, worker, or static site to AWS.
- Writing or reviewing an IAM policy, role, or trust relationship.
- Designing a VPC, subnets, routing, or security groups.
- Choosing between EC2, ECS/Fargate, EKS, and Lambda.
- Storing objects in S3 and controlling cost or access with lifecycle rules.
- Provisioning a relational database and understanding its failover modes.
- Putting a load balancer in front of an application.
- Storing and rotating secrets.
- Setting up CloudWatch alarms, dashboards, or logs.
- Estimating cost or running a Well-Architected review.

## Quick start

A serverless containerized service with least-privilege execution role, private
storage, and managed database.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadApplicationConfig",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:us-east-1:123456789012:secret:acme/prod/db-*"
    },
    {
      "Sid": "WriteObjectData",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:AbortMultipartUpload"
      ],
      "Resource": "arn:aws:s3:::acme-prod-data/uploads/*"
    },
    {
      "Sid": "DenyUnencryptedTransport",
      "Effect": "Deny",
      "Action": "sts:AssumeRole",
      "Condition": {
        "Bool": { "aws:SecureTransport": "false" }
      }
    }
  ]
}
```

```bash
# Authenticate without long-lived keys
aws sso login --profile acme-prod
export AWS_PROFILE=acme-prod AWS_REGION=us-east-1

aws sts get-caller-identity        # always check who you actually are
aws configure set region us-east-1 --profile acme-prod
aws --version                     # CLI v2 recommended
```

## Identity first

| Primitive | What it is | Use it when |
|---|---|---|
| IAM user | Long-lived credentials for a person | Almost never |
| IAM role | Temporary credentials assumed by a principal | Default for services and workloads |
| IAM group | Container for IAM users | Legacy; manage user permissions centrally |
| Root account | Full access, break-glass only | Never for daily work |
| Policy | Attached to an identity or resource | Granting or denying actions |
| Trust policy | Which principals may assume a role | Role-to-role and service-to-role links |
| SCP | Organization-wide ceiling on permissions | Guardrails across accounts |
| Permission boundary | Maximum an identity can ever have | Delegating administration safely |
| Session | Assumed role, with an expiry and an external ID | Distinguishing tenants or integrations |

### Least privilege in practice

- Roles over users: credentials expire automatically and are never stored.
- One role per workload, not one role per team or per environment pipeline.
- Policy scope: prefer resource-level permissions (`s3:GetObject` on a specific object
  ARN) over service-wide permissions with a `Resource: "*"`.
- Conditions narrow further: source VPC endpoint, MFA, IP range, tags, or
  `aws:PrincipalTag`. Conditions are where least privilege becomes precise
  least privilege.
- Separate the three questions: who (principal), what (action), where (resource).
- Start from the AWS-provided managed policies for read access when reviewing
  third-party integrations, and cut them down.

```bash
aws iam get-role-policy --role-name acme-api-role --policy-name Inline
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/acme-api-role \
  --action-names s3:PutObject s3:DeleteBucket
aws iam generate-credential-report       # lists users and their credential age
aws accessanalyzer --analyzer-name acme-analyzer          # external/false findings
aws sts get-session-duration
```

`simulate-principal-policy` is the most useful IAM tool and the least used. Simulate
the exact API calls your application makes before shipping.

### Credential hygiene

- Prefer federation: OIDC from CI, SSO for humans, and instance/task/pod roles for
  workloads.
- If a static access key is unavoidable, rotate it on a schedule and prefer role
  credentials with an `ExternalId` when a third party assumes the role.
- IMDSv2 (`HttpTokens=required`) blocks the SSRF-to-credential-theft pattern of
  fetching a role's credentials from a metadata URL.
- Never put an access key in a repository, a container image, a CI log, or a
  Terraform variable file.

```bash
aws ec2 modify-instance-metadata-options \
  --instance-id i-0123456789abcdef0 \
  --http-tokens required \
  --http-endpoint enabled
```

## Compute selection

| Service | You manage | Scales | Cold start | Best fit |
|---|---|---|---|---|
| EC2 | Guest OS, capacity, patching | Manual or ASG | None | Long-running, unpredictable, specialized workloads |
| ECS on Fargate | Task and container image | Service autoscaling | Seconds | Containers without a cluster to operate |
| EKS | Kubernetes | HPA, cluster autoscaler | Seconds | You want Kubernetes APIs and portability |
| Lambda | Function and memory | Concurrency | Hundreds of ms | Event-driven, spiky, sub-15-minute tasks |

Decision path:

1. Does it need a specific OS feature, a GPU, or a long-lived connection? **EC2**.
2. Is it a container and do you not want to run a Kubernetes control plane?
   **ECS on Fargate**, or ECS on EC2 when you need host-level control or
   cost-optimized capacity.
3. Do you already operate Kubernetes, or need its ecosystem? **EKS**.
4. Is it event-driven, spiky, and short-lived? **Lambda**.
5. Is it a static site? S3 plus CloudFront, possibly with a Lambda@Edge or a small
   Lambda for dynamic rewrites.

Lambda has a hard maximum execution timeout documented on its service limits page,
and cold starts scale with the memory you allocate and with how far your package is
from the user. Verify current limits and supported runtimes before designing around
either.

## VPC and networking

```
                    Internet
                       |
              Internet Gateway
                       |
      +----------------+----------------+
      |                                 |
  Public subnet A                Public subnet B
  (ALB, NAT Gateway)             (ALB, NAT Gateway)
      |                                 |
      +-------- Private subnets --------+
        (ECS tasks, RDS)   spread across availability zones
                         |
                    VPC endpoints (S3, Secrets Manager)
```

Subnets: a subnet is a subnet **of a range in a route table**. Call it "public"
when its route table sends default traffic to an Internet gateway; call it "private"
when outbound traffic goes through a NAT gateway or a VPC endpoint and inbound
internet traffic cannot reach it directly.

Key objects and their failure modes:

| Object | Purpose | Common mistake |
|---|---|---|
| Route table + association | Where traffic from a subnet goes | Private subnet with a route to the IGW, so nothing is really private |
| Security group | Stateful per-instance allow/deny | Opening `0.0.0.0/0` on an admin port "temporarily" |
| Network ACL | Stateless per-subnet allow/deny | Assumed to be stateful; forgetting return-traffic rules |
| NAT gateway | Outbound-only internet for private subnets | One per AZ in production; a single NAT is an AZ-sized failure domain |
| VPC endpoint | Private service access without NAT | Missing S3 gateway endpoint, so S3 traffic is paying for NAT |
| VPC peering | Private connectivity between VPCs | Assuming transitive routing; it is not |
| Transit Gateway | Hub-and-spoke across many VPCs | Overused for two VPCs |
| Endpoint-only DNS / prefix lists | Private hosted zones for services | Mixing endpoint-only with interface endpoints |

```bash
aws ec2 describe-vpcs --filters 'Name=state,Values=available'
aws ec2 describe-subnets --filters 'Name=vpc-id,Values=vpc-0123456789abcdef0'
aws ec2 describe-route-tables
aws ec2 describe-security-groups --group-names acme-api-sg
aws ec2 describe-vpc-endpoints --filters 'Name=vpc-id,Values=vpc-0123456789abcdef0'
aws ec2 describe-nat-gateways
aws ec2 describe-internet-gateways
aws ec2 describe-vpc-peering-connections
```

Rules of thumb: one NAT gateway per availability zone for production (or a NAT
instance per AZ if cost dominates and your availability requirements allow it);
default-deny on security groups and add specific rules; VPC interface endpoints are
cheaper than routing service traffic through NAT, and the S3 gateway endpoint is
free. Verify current endpoint and NAT pricing before assuming.

## S3

```bash
aws s3 mb s3://acme-prod-data --region us-east-1
aws s3api put-public-access-block \
  --bucket acme-prod-data \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,\
BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-versioning --bucket acme-prod-data \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket acme-prod-data \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms","KMSMasterKeyID":"alias/acme-data"}}]}'
```

Storage classes, as a shape rather than a price list:

| Class | Shape |
|---|---|
| Standard | Default; immediate access, lowest latency |
| Standard-IA | Cheaper, retrieval latency applies, a minimum duration per object |
| One Zone-IA | Infrequent access in a single AZ; lose the data, lose the AZ |
| Intelligent-Tiering | Moves objects between access tiers automatically; small per-object monitoring cost |
| Glacier Instant | Archive class with millisecond retrieval |
| Glacier Flexible | Larger objects, retrieval takes minutes |
| Glacier Deep Archive | Lowest cost, retrieval takes hours |

Lifecycle rules are the real cost lever, because they move objects automatically as
they age:

```json
{
  "Rules": [
    {
      "ID": "tier-cold-data",
      "Status": "Enabled",
      "Filter": { "Prefix": "uploads/" },
      "Transitions": [
        { "Days": 30, "StorageClass": "STANDARD_IA" },
        { "Days": 120, "StorageClass": "GLACIER_FLEXIBLE" }
      ],
      "NoncurrentVersionExpiration": { "NoncurrentDays": 30 },
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 7 },
      "Expiration": { "Days": 2555, "ExpiredObjectDeleteMarker": true }
    }
  ]
}
```

Points that cause real incidents: minimum storage duration and minimum object size
mean an IA-class object deleted early still incurs charges; lifecycle does not apply
retroactively well enough to save a bucket that already accumulated hot objects;
enabling versioning multiplies storage; incomplete multipart uploads accumulate
silently and are a common cause of a mystery bill. Always set
`AbortIncompleteMultipartUpload`.

Other controls: Object Lock for compliance retention, replication for cross-region
durability, event notifications for downstream processing, and presigned URLs for
time-limited access. Block all public access on every bucket by default.

## RDS and Aurora

```bash
aws rds create-db-instance \
  --db-instance-identifier acme-prod \
  --db-instance-class db.r6g.large \
  --engine postgres \
  --engine-version 16 \
  --allocated-storage 100 \
  --storage-type gp3 \
  --multi-az \
  --db-subnet-group-name acme-prod-subnets \
  --vpc-security-group-ids sg-0123456789abcdef0 \
  --master-username dbadmin \
  --manage-master-user-password \
  --backup-retention-period 14 \
  --storage-encrypted \
  --performance-insights-enabled \
  --enable-cloudwatch-logs-exports postgresql upgrade

aws rds describe-db-instances --db-instance-identifier acme-prod \
  --query 'DBInstances[0].[DBInstanceStatus,Endpoint.Address,MultiAZ,StorageEncrypted]'
```

- **Multi-AZ** on a standard RDS instance is a synchronous standby in another AZ with
  a different endpoint. It is for availability, not for read scaling. Standby nodes
  are not readable.
- **Read replicas** are for read scaling and, with the right configuration, for
  cross-region standby. A replica can be promoted to become a new writer, but the
  topology change is real work.
- **Aurora** separates the cluster from instances, so storage scales independently and
  replicas can be added or promoted relatively cheaply. Aurora Serverless is a
  capacity-range model, not a magic auto-scaler; verify its scaling behavior for your
  workload.
- Connection exhaustion is a real production failure. Use RDS Proxy or a pooler
  (PgBouncer, ProxySQL) between the application and the database, and set
  `max_connections` deliberately.
- Use IAM database authentication or store credentials in Secrets Manager with
  rotation, never in application configuration files.
- Verify engine version support and regional availability for features you need.

## Load balancing

| Type | Layer | Use for | Notes |
|---|---|---|---|
| Application Load Balancer | 7 | HTTP/HTTPS routing, host and path rules, WebSocket, gRPC | TLS termination, WAF integration |
| Network Load Balancer | 4 | TCP/UDP passthrough, extreme performance, static IPs per AZ | Can pass through TLS |
| Gateway Load Balancer | 3/7 | Deploy and scale third-party virtual appliances | Higher per-hour cost |
| Classic Load Balancer | 4/7 | Legacy only | Do not choose for new work |

```bash
aws elbv2 create-target-group --name acme-api --protocol HTTP --port 8080 \
  --vpc-id vpc-0123456789abcdef0 --target-type ip \
  --health-check-path /healthz --health-check-interval-seconds 10 \
  --health-check-timeout-seconds 5 --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --target-group-attributes stickiness.enabled=false
```

Health checks are the difference between a load balancer that heals and one that
routes into a black hole. Point them at a real readiness endpoint, and remember a
slow health check adds seconds to failure detection: interval plus timeout times the
threshold, plus detection of the unhealthy pod. For an ECS service, the target group
type is `ip` with `awsvpc` network mode.

Deregistration delay must be longer than your application needs to drain, or the load
balancer closes connections mid-request.

## Secrets

```bash
aws secretsmanager create-secret --name acme/prod/db \
  --generate-secret-string '{"passwordLength":40,"excludeCharacters":"\"@/\\"}'

aws secretsmanager get-secret-value --secret-id acme/prod/db --query SecretString --output text
aws secretsmanager rotate-secret --secret-id acme/prod/db \
  --rotation-lambda-arn arn:aws:lambda:us-east-1:123456789012:function:acme-rotator

aws secretsmanager put-secret-value --secret-id acme/prod/db \
  --secret-string '{"password":"newvalue"}' \
  --client-request-token "$(uuidgen)"
```

Secrets Manager is integrated with RDS (managed master password), ECS task
definitions, Lambda environment variables, and CloudFormation dynamic references.
Secrets Manager and Parameter Store (SecureString) overlap; prefer Secrets Manager
when you need rotation and structured JSON secrets.

Secrets are cached. After rotating, your application needs to pick up the new value:
cache with a TTL and a jittered refresh, or a sidecar/reload hook. Rotation without a
reload plan is an outage waiting for its trigger.

## CloudWatch

```bash
aws logs create-log-group --log-group-name /ecs/acme-api \
  --log-group-class STANDARD --retention-in-days 30
aws cloudwatch put-metric-alarm --alarm-name acme-api-5xx-high \
  --namespace AWS/ApplicationELB --metric-name HTTPCode_Target_5XX_Count \
  --dimensions Name=TargetGroup,Value=acme-api-abc123 \
  --statistic Sum --period 300 --evaluation-periods 3 \
  --threshold 50 --comparison-operator GreaterThanThreshold \
  --alarm-actions arn:aws:sns:us-east-1:123456789012:acme-alerts
```

```bash
aws logs start-query --log-group-name /ecs/acme-api \
  --start-time "$(($(date +%s) - 3600))" --end-time "$(date +%s)" \
  --query-string 'fields @timestamp, status, latency
                 | filter status >= 500
                 | stats count(), avg(latency) by bin(5m)'
aws logs get-query-results --query-id <id>
```

Practice that pays off: set log group retention at creation (the default is never
expire); alert on symptom metrics users feel, not only cause metrics; use composite
alarms for a real outage condition; use anomaly detection on noisy, seasonal
signals; and make sure something alerts on the alerting pipeline itself.

## Cost awareness

You do not need exact prices. You need the levers.

```bash
aws ce get-cost-and-usage --time-period Start=2026-01-01,End=2026-02-01 \
  --granularity MONTHLY --metrics BlendedCost \
  --group-by Type=DIMENSION,Key=SERVICE

aws ce create-anomaly-detection --subscription '{"Frequency":"DAILY","AccountId":"..."}'
aws budgets create-budget --budget-name acme-monthly \
  --budget-type COST --limit 5000 --time-unit MONTHLY \
  --notifications-with-subscribers NotificationWithSubscribers='[{"Notification":{"ComparisonOperator":"GREATER_THAN","Threshold":80,"NotificationType":"ACTUAL"},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"finops@acme.example"}]}]'
```

Levers, ranked by typical impact:

1. **Tag everything at creation** and activate tags you intend to filter by. Cost
   allocation is impossible without tags. Enforce with SCPs or a policy-as-code
   gate in the deployment pipeline.
2. **Right-size** from observed utilization, not from instance family defaults.
   Compute Optimizer and Trusted Advisor are starting points, not answers.
3. **Match purchase model to workload.** On-demand for baseline and spiky critical
   work; Savings Plans or Reserved Instances for steady usage; Spot for fault-tolerant
   batch. Spot interruptions are real; build interruption handling before you
   depend on it.
4. **Storage lifecycle** in S3, including aborting incomplete multipart uploads.
5. **Data transfer**: cross-AZ traffic, NAT gateway processing, and cross-region
   replication are frequently the largest surprise line items.
6. **Idle resource removal**: unattached EBS volumes, unused Elastic IPs, idle load
   balancers, forgotten staging environments, old snapshots.

Prefer Graviton-based instance families where your software is supported and
benchmarked. Check current generation instance types and prices in the pricing
calculator rather than assuming a family is cheapest.

## Well-Architected review

Six pillars. Use them as a checklist for any architecture you are reviewing, in any
cloud.

| Pillar | Questions |
|---|---|
| Operational excellence | Can you deploy, observe, and recover without heroics? Is there a runbook per alert? |
| Security | Least privilege, encryption at rest and in transit, secrets centralized, audit trails, blast radius |
| Reliability | Failure modes identified, tested, and recoverable; no single points of failure; quotas understood |
| Performance efficiency | Resources chosen by measurement, right-sized, using the right instance or storage class |
| Cost optimization | Tagged, allocated, right-sized, appropriate purchase model, waste removed |
| Sustainability | Right-sized for the work; idle and over-provisioned capacity removed |

A useful first pass: run the workload through the questions "what if this AZ is
lost", "what if this region is lost", "what if the person who set this up leaves",
and "what does this cost when it is ten times larger".

## Checklist

- Root account unused, MFA everywhere, no long-lived access keys in use.
- Every workload has its own role with resource-scoped permissions and conditions.
- IMDSv2 required on instances that serve workloads.
- VPC with public subnets only for load balancers and NAT; private subnets for
  compute and databases.
- Default-deny security groups; no `0.0.0.0/0` on administrative ports.
- S3: public access blocked, versioning and lifecycle set, multipart uploads aborted.
- Database: Multi-AZ or a documented reason not to, backups verified by restore,
  connection pooling in front of it.
- Secrets in Secrets Manager with rotation, and an application reload plan.
- Log groups created with retention.
- Alarms on user-visible symptoms, plus an alarm on the alarm pipeline.
- Budgets and anomaly detection configured with a real owner.
- Resource tags applied at creation and required by policy.
- Drift detected: infrastructure reconciled from code, not by hand.

## Common mistakes

**Symptom: `AccessDenied` on an action the policy appears to allow.**
Cause: the resource ARN is wrong, a condition key does not match, an SCP or
permission boundary is denying it, or the role session is expired. Fix:
`aws sts get-caller-identity`, then `aws iam simulate-principal-policy`.

**Symptom: instances cannot reach the internet from a private subnet.**
Cause: no NAT gateway, no route from the private route table to the NAT, or the NAT
gateway is in an AZ the subnet does not use efficiently. Fix: check route table
associations and NAT gateway state.

**Symptom: latency spikes between the app and the database.**
Cause: cross-AZ traffic because the app and database landed in different AZs, or a
connection-per-request client. Fix: place them in the same AZ subnet, or accept the
cross-AZ cost deliberately and use endpoints.

**Symptom: a rolling deployment caused a burst of 502s.**
Cause: deregistration delay shorter than the application drain time, or the health
check endpoint returns 200 before the app is ready. Fix: raise deregistration delay
and add a readiness gate.

**Symptom: unexpected charges on S3.**
Cause: incomplete multipart uploads, versioned objects never expiring, IA-class
objects deleted before the minimum duration, or lifecycle rules that only apply to
new objects. Fix: set abort rules, noncurrent version expiration, and re-evaluate
classes after enabling lifecycle.

**Symptom: `The engine version ... is not supported` or a region lacks a feature.**
Cause: regional availability or feature parity differences. Fix: check the regional
services and feature matrix in the documentation for the specific region.

**Symptom: rotated secret caused authentication failures.**
Cause: the application cached the secret indefinitely. Fix: add a bounded cache with
jittered refresh, or a reload signal.

## Anti-patterns

- Root account or an IAM user with `AdministratorAccess` used for daily work.
- `"Action": "*", "Resource": "*"` in a policy attached to a running workload.
- Long-lived access keys in environment variables, images, or CI variables, when
  federation was available.
- Public S3 buckets, even "temporarily". Assume the temporary is permanent.
- Security groups with `0.0.0.0/0` on port 22 or 3389. Use SSM Session Manager or a
  bastion.
- Secrets in environment variables of a task definition that anyone with
  `ecs:DescribeTaskDefinition` can read; use a secret reference instead.
- Single NAT gateway in a multi-AZ production design.
- Untagged resources, which makes cost allocation impossible after the fact.
- Manual console changes in production without a matching code change.
- Trusting "the Trusted Advisor says it is right" without measuring your own
  utilization.
- Alerting on CPU or every metric. Alert on symptoms and on exhaustion of capacity
  headroom.

## References

- IAM policy reference: https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies.html
- IAM policy simulator: https://docs.aws.amazon.com/policy-simulator/latest/userguide/
- Least privilege guide: https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html
- VPC: https://docs.aws.amazon.com/vpc/latest/userguide/
- EC2 user guide: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/
- Lambda: https://docs.aws.amazon.com/lambda/latest/dg/
- ECS: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/
- EKS: https://docs.aws.amazon.com/eks/latest/userguide/
- S3 user guide: https://docs.aws.amazon.com/AmazonS3/latest/userguide/
- S3 storage classes and lifecycle: https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-classes.html
- RDS user guide: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/
- Aurora: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/
- Elastic Load Balancing: https://docs.aws.amazon.com/elasticloadbalancing/latest/userguide/
- Secrets Manager: https://docs.aws.amazon.com/secretsmanager/latest/userguide/
- CloudWatch: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/
- CloudWatch Logs Insights query reference: https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_Query_Syntax.html
- Well-Architected Framework: https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html
- Cost Optimization pillar: https://docs.aws.amazon.com/wellarchitected/latest/cost-optimization-pillar/welcome.html
- Service quotas: https://docs.aws.amazon.com/servicequotas/latest/userguide/what-is.html
- AWS CLI reference: https://docs.aws.amazon.com/cli/latest/reference/

Verify before relying on this: service availability, instance generations, limits,
and pricing change frequently. Check the linked documentation for the region you
are deploying to, confirm CLI flags with `aws <service> <operation> help`, and use
the pricing calculator in the console for any figure you intend to put in a business
case.