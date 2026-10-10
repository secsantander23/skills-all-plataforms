# Module Design and Reference

## A well-formed module

```
modules/network/
  main.tf          # resources only
  variables.tf     # inputs with types, defaults, validation, descriptions
  outputs.tf       # outputs with descriptions and sensitivity marked
  versions.tf      # required_version and required_providers
  README.md        # usage example, contract, lifecycle
```

`versions.tf`:

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}
```

`variables.tf`:

```hcl
variable "name_prefix" {
  type        = string
  description = "Prefix applied to every resource name. Must be globally unique."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name_prefix))
    error_message = "name_prefix must be lowercase alphanumeric with dashes, 3-31 chars."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "IPv4 CIDR for the VPC."

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR."
  }
}

variable "availability_zones" {
  type        = list(string)
  description = "Availability zones to spread subnets across."

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "Provide at least two availability zones for zone redundancy."
  }
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Tags applied to every taggable resource."
}
```

`main.tf` highlights:

```hcl
locals {
  all_tags = merge(var.tags, { ManagedBy = "terraform", Module = "network" })
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.all_tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.all_tags, {
    Name = "${var.name_prefix}-public-${count.index + 1}"
    Tier = "public"
  })
}

# for_each variant used when instances are not interchangeable
resource "aws_nat_gateway" "this" {
  for_each = toset(var.availability_zones)

  allocation_id = aws_eip.nat[each.key].id
  subnet_id     = aws_subnet.public[each.key].id
  tags          = merge(local.all_tags, { Name = "${var.name_prefix}-nat-${each.key}" })

  depends_on = [aws_internet_gateway.this]
}
```

Note the `depends_on` on the NAT gateways: it exists because a NAT gateway needs
the internet gateway to exist, and nothing in the argument list expresses that.

## Importing existing infrastructure

Adoption is safer than recreation. Two shapes:

```bash
# Imperative, older and always available
terraform import aws_s3_bucket.artifacts acme-prod-artifacts
terraform import 'module.network.aws_vpc.this[0]' vpc-0123456789abcdef0
terraform import 'module.network.aws_subnet.public["us-east-1a"]' subnet-0123456789abcdef0
```

```hcl
# Declarative, supported in newer versions. Check availability and plan output
# with your version before adopting.
import {
  to = aws_s3_bucket.artifacts
  id = "acme-prod-artifacts"
}

resource "aws_s3_bucket" "artifacts" {
  bucket = "acme-prod-artifacts"
  tags   = merge(local.all_tags, { Name = "acme-prod-artifacts" })

  lifecycle {
    prevent_destroy = true
  }
}
```

Adoption procedure that works:

1. Write the resource block from observed reality. Read the live configuration, not
   what you wish it were.
2. `terraform import` (or add the import block).
3. `terraform plan` and expect **no changes**. Any change here means your
   configuration and reality disagree; reconcile before going further.
4. Only then change the configuration to the desired state.

## State splitting

One state per blast-radius boundary. Common split:

```
infra-terraform/
  envs/
    dev/      backend.hcl  main.tf  outputs.tf
    staging/  backend.hcl  main.tf  outputs.tf
    prod/     backend.hcl  main.tf  outputs.tf
  modules/
  policy/
```

```hcl
# envs/prod/backend.hcl
bucket = "acme-terraform-state"
key    = "prod/app/terraform.tfstate"
region = "us-east-1"
encrypt = true
```

```bash
cd envs/prod
terraform init -backend-config=backend.hcl
```

Split when a team boundary, a change-approval boundary, or a failure domain requires
it. Do not split so far that each resource has its own state; that trades one problem
for hundreds of tiny plans.

## Policy as code in the plan

Conceptual OPA/Rego policy evaluated against a JSON plan:

```rego
package terraform.deny

deny[msg] {
  rc := input.resource_changes[_]
  rc.type == "aws_s3_bucket"
  not rc.change.after.server_side_encryption_configuration
  msg := sprintf("%s creates an S3 bucket without encryption configuration", [rc.address])
}

deny[msg] {
  rc := input.resource_changes[_]
  rc.type == "aws_db_instance"
  rc.change.after.publicly_accessible == true
  msg := sprintf("%s is publicly accessible", [rc.address])
}

deny[msg] {
  rc := input.resource_changes[_]
  rc.change.destroy == true
  not rc.change.before.prevent_destroy
  rc.type == "aws_db_instance"
  msg := sprintf("%s would be destroyed", [rc.address])
}
```

```bash
terraform plan -out=tfplan
terraform show -json tfplan > tfplan.json
conftest test tfplan.json -p policy/
```

Add `--update` to the command only when you are changing the policy intentionally.

## Drift triage worksheet

| Finding | Question | Action |
|---|---|---|
| Security group rule added outside Terraform | Intended hardening or compromise? | Revert or accept and codify |
| Instance size changed manually | Emergency perf fix? | Import the change or fold it into configuration |
| Bucket lifecycle removed | Accidental or compliance-driven? | Revert, or update configuration to match policy |
| Autoscaler-created tags | Machine-managed and expected | `ignore_changes` for those tag keys |
| Database engine minor version upgrade | Provider-managed maintenance | Ignore the specific attribute; document it |

Record the decision next to the resource. The next engineer needs to know whether a
drift alert is a bug or a policy.