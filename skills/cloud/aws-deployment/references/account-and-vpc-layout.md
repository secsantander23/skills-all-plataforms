# AWS Account and Network Layout

## Account structure

```
Organization
├── Root account (break-glass only, no daily use)
├── Management account (SCPs, organization-wide IAM, GuardDuty org-wide)
├── Security account (audit logs, central SIEM, key management)
└── Per-environment accounts
    ├── dev
    ├── staging
    ├── prod
    ├── shared-services
    └── sandbox
```

Why separate accounts rather than separate regions in one account:

- Blast radius. A policy error or compromised principal in dev cannot reach prod.
- Blast radius of an *error*, not just an attack. `aws iam delete-account-password-policy`
  applied in the wrong account is unrecoverable at the shared-IAM level.
- Billing isolation and per-account budgets.
- Region and service availability constraints do not apply the same way.
- AWS Config and CloudTrail can be centralized through organization trails while
  resources stay partitioned.

Cost consequence: one account per environment means your committed-use discounts and
savings commitments cannot span accounts as easily. That trade is real; decide it
deliberately rather than by accident.

## Baseline guardrails per account

```bash
# Block root user access keys, enforce MFA
aws organizations update-organization-policy \
  --policy-id p-xxxxxxx \
  --content file://scp-root-mfa.json

# Central CloudTrail (organization trail)
aws cloudtrail create-trail \
  --name org-trail --s3-bucket-name acme-audit-trail-xxx \
  --include-global-service-events --is-multi-region-trail \
  --kms-key-id alias/acme-audit

# Default-deny guardrail template
```

Guardrails to have from day one:

| Control | Scope | Notes |
|---|---|---|
| Root MFA required, no root access keys | Org-wide | SCP with a condition on `aws:MultiFactorAuthPresent` |
| Restrict full `*:*` permissions | Org-wide | SCP denying wildcard actions, with an explicit exception path |
| Block resource creation without required tags | Org-wide | SCP with `aws:RequestTag` conditions, or an EventBridge-driven control |
| Block unencrypted storage | Per region | SCP denying the unencrypted actions, or a Config rule |
| Restrict regions | Org-wide | SCP with `aws:RequestedRegion`, with exceptions per account |
| Block root-ish actions in workload accounts | Org-wide | Deny `iam:*` and `organizations:*` to non-admin principals |

SCP deny statements take precedence over everything else in the account, including
an explicit allow. That is a feature: an SCP deny is a ceiling, not a suggestion.

## Tagging strategy

Define tags before you need them. Retrofitting tags across resources is painful and
partial.

```
Owner:        team-payments          # who can answer questions
CostCenter:   cc-4471               # finance allocation
Environment:  prod
Application:  acme-api
Service:      api
ManagedBy:    terraform
DataClass:    confidential
```

Enforcement:

1. Define tag keys in an AWS Organizations tag policy; tag policies are authoritative
   for values and can require tags on taggable resource creation.
2. Add an SCP requiring `aws:RequestTag` for the keys you require.
3. Enforce in code: the deployment pipeline rejects resources without required tags
   before they reach the account.
4. Activate the tags you intend to use in Cost Explorer, or they will not appear as
   grouping dimensions.

```bash
aws organizations create-policy \
  --name RequiredTags --type TAG_POLICY \
  --content file://tag-policy.json
aws organizations describe-effective-policy --policy-type TAG_POLICY
aws ce get-cost-and-usage \
  --time-period Start=2026-01-01,End=2026-02-01 \
  --group-by Type=TAG,Key=CostCenter
```

## VPC layout for a three-tier service

```
vpc 10.20.0.0/16
  public-a   10.20.0.0/24    in us-east-1a   route 0.0.0.0/0 -> igw
  public-b   10.20.16.0/24   in us-east-1b   route 0.0.0.0/0 -> igw
  private-a  10.20.1.0/24    in us-east-1a   route 0.0.0.0/0 -> nat-a
  private-b  10.20.17.0/24   in us-east-1b   route 0.0.0.0/0 -> nat-b
  isolated-a 10.20.2.0/24    in us-east-1a   no default route; endpoints only
  isolated-b 10.20.18.0/24   in us-east-1b
```

Route table organization:

| Route table | Associated with | Default route | Purpose |
|---|---|---|---|
| `public` | public-a, public-b | Internet gateway | Load balancer, NAT gateway |
| `private` | private-a, private-b | NAT gateway (per AZ) | Application tasks, outbound only |
| `isolated` | isolated-a, isolated-b | none | Databases; reaches only VPC endpoints |

Endpoints that belong in `isolated`:

- Interface endpoints for Secrets Manager, SSM, STS, CloudWatch Logs. They need
  security groups and per-hour plus per-GB charges.
- Gateway endpoints for S3 and DynamoDB, added to the route table rather than as a
  separate security group. No hourly charge; traffic avoids NAT and its processing
  charges.

```bash
aws ec2 create-route-table --vpc-id vpc-... \
  --tag-specifications 'ResourceType=route-table,Tags=[{Key=Name,Value=acme-private}]'

aws ec2 create-route --route-table-id rtb-... \
  --destination-cidr-block 0.0.0.0/0 --gateway-id nat-...

aws ec2 create-vpc-endpoint --vpc-id vpc-... \
  --service-name com.amazonaws.us-east-1.s3 \
  --route-table-ids rtb-...

aws ec2 modify-vpc-endpoint --vpc-endpoint-id vpce-... \
  --policy-document file://s3-endpoint-policy.json
```

The S3 endpoint policy is the piece people miss. Without it, an endpoint that allows
all principals on all buckets removes a layer of control. Scope it to your bucket
ARNs.

## Security groups, correctly

Default-deny as the baseline, then add what is needed.

```bash
# Ingress: default deny all
aws ec2 create-security-group --group-name deny-all \
  --description "Default deny" --vpc-id vpc-...

# Application: accept from the load balancer security group only
aws ec2 authorize-security-group-ingress --group-id sg-app \
  --protocol tcp --port 8080 --source-group sg-alb

# Load balancer: 443 from anywhere, 80 only to redirect
aws ec2 authorize-security-group-ingress --group-id sg-alb \
  --protocol tcp --port 443 --cidr 0.0.0.0/0

# Database: accept from the application security group only
aws ec2 authorize-security-group-ingress --group-id sg-db \
  --protocol tcp --port 5432 --source-group sg-app
```

Referencing security groups instead of CIDRs is what makes rules survive IP changes
and read clearly. Network ACLs are stateless and subnet-level; use them only when you
genuinely need deny rules or subnet-level segmentation, and remember to allow the
return direction explicitly.

## Private service access and hybrid

```bash
aws ec2 create-vpc-endpoint --vpc-id vpc-... \
  --service-name com.amazonaws.us-east-1.elasticloadbalancing \
  --vpc-endpoint-type Interface \
  --private-dns-enabled true \
  --subnet-ids subnet-isolated-a subnet-isolated-b \
  --security-group-ids sg-endpoints
```

On-premises connectivity options, roughly in order of operational cost: a VPN
connection; a direct connect connection; direct connect gateway with transit
gateway for multi-site. Verify current pricing and regional availability before
choosing; direct connect has a long lead time and is usually reserved rather than
hosted.