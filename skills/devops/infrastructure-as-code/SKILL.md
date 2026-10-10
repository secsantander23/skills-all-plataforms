---
name: Infrastructure as Code
description: Covers infrastructure as code with Terraform and HCL, resource modeling, state management and remote state with locking, the plan and apply workflow, modules and reuse, drift detection, workspaces, variable sensitivity, safe change review, policy checks, and alternatives such as OpenTofu, Pulumi, CloudFormation, and Ansible. Use when the user asks about writing Terraform, terraform plan or apply errors, state locking, remote state backends, reusable modules, managing infrastructure drift, or choosing between IaC tools.
metadata:
  opencode/autoinvoke: true
---

# Infrastructure as Code

Infrastructure as code turns infrastructure changes into reviewed, versioned,
reproducible diffs. The tool is not the point; the point is that a change to
production is a pull request with a plan attached, applied by a known identity, and
reconciled automatically when reality drifts.

The mental model: **configuration is desired state, state is observed state, and
`plan` is the diff between them.**

## When to use this

- Writing or reviewing Terraform or HCL configuration.
- Understanding a `terraform plan` diff, including ones that destroy more than you
  expected.
- State locking, remote state, backends, or a `State lock` error.
- Extracting or moving a resource between states.
- Packaging shared infrastructure into a module.
- Detecting that someone changed a resource by hand.
- Deciding between Terraform, OpenTofu, Pulumi, CloudFormation, or Ansible.

## Quick start

A complete, valid Terraform configuration.

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"   # pessimistic constraint on the provider major
    }
  }

  backend "s3" {
    bucket       = "acme-terraform-state"
    key          = "prod/network/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    # Locking options differ by backend and version. Check the backend docs for
    # the Terraform version you run; S3-native lockfile support was added after
    # the older DynamoDB table approach.
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type        = string
  default     = "us-east-1"
  description = "Deployment region."
}

variable "environment" {
  type = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of dev, staging, prod."
  }
}

variable "db_password" {
  type      = string
  sensitive = true
}

locals {
  name_prefix = "acme-${var.environment}"
  tags = {
    Project     = "acme"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket" "artifacts" {
  bucket = "${local.name_prefix}-artifacts"
  tags   = merge(local.tags, { Name = "${local.name_prefix}-artifacts" })
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_kms_key" "artifacts" {
  description             = "Encrypts ${local.name_prefix} artifacts"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  tags                    = local.tags
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.artifacts.arn
    }
  }
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "artifacts_arn" {
  value = aws_s3_bucket.artifacts.arn
}
```

```bash
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -out=tfplan
terraform show tfplan            # human-readable plan
terraform show -json tfplan > tfplan.json   # machine-readable, for policy gates
terraform apply tfplan
```

Note the deliberate habit: `plan -out` to a file, then `apply` **that file**. It
guarantees you apply exactly what you reviewed, with no window for the plan to change
underneath you.

## The plan/apply contract

| Command | Purpose |
|---|---|
| `terraform init` | Download providers, configure the backend, install modules |
| `terraform fmt` | Rewrite files into canonical style |
| `terraform validate` | Structural check of the configuration |
| `terraform plan` | Compute the diff; touches real APIs to refresh state |
| `terraform apply` | Make the diff real |
| `terraform destroy` | Plan the destruction of everything in state |

- `terraform plan -detailed-exitcode` returns `0` for no changes, `1` for error,
  `2` when changes are present. This is the CI gate form.
- `terraform apply tfplan` refuses flags like `-var` because the plan is already
  fixed. That is a feature.
- `terraform apply -auto-approve` is for automation inside a controlled pipeline
  after a human reviewed the plan. It is not a shortcut for local work.
- `terraform plan` **refreshes state by default**, so it makes read API calls. Plan
  from a CI role with read access, not an apply role.

Always read a plan before applying it. The lines to search for:

```bash
terraform show -no-color tfplan | grep -nE '# .*will be destroyed|# .*must be replaced'
terraform show -no-color tfplan | grep -nE 'forces replacement'
```

Anything with "will be destroyed" or "forces replacement" is a change with an
availability consequence.

## State management

State is a serialized snapshot of every resource Terraform manages. It contains
values in plain text, including anything you marked `sensitive`, because the secret
must be stored somewhere. Consequences:

- State is as sensitive as the most sensitive resource it manages.
- Enable encryption and versioning on the state store. Versioning is your rollback
  path when someone corrupts state.
- Never commit state to git. Add `*.tfstate`, `*.tfstate.*`, and `.terraform/` to
  `.gitignore` unless the repository is a dedicated, encrypted state backend.
- Never edit state by hand.

Backend types include local (default), `s3`, `gcs`, `azurerm`, and `http` (including
remote services). Switching a backend requires reinitializing:

```bash
terraform init -migrate-state -backend-config=bucket=acme-terraform-state
terraform init -reconfigure     # discard the current backend config
```

### Locking

Two concurrent `apply` operations against the same state produce corruption, not
merge conflicts. Every backend offers a locking mechanism; verify which one your
version and backend use.

```bash
terraform force-unlock <lock-id>
```

Only use `force-unlock` when you have confirmed no other apply is running. Force
unlocking a live operation is how two processes write the same state.

### Useful state commands

```bash
terraform state list
terraform state show aws_s3_bucket.artifacts
terraform state pull > state.json
terraform state push state.json          # dangerous; back up first
terraform state mv aws_instance.old aws_instance.new   # rename without destroy
terraform state rm aws_instance.orphan                  # stop managing, leave in place
terraform import aws_s3_bucket.artifacts acme-prod-artifacts
```

`terraform state mv` and `import` are the two operations that let you adopt existing
infrastructure without destroying and recreating it. Many tools also support
declarative `import` blocks; check whether your version supports them and what
plan output they produce before adopting.

## Resource modeling

- One logical thing per `resource` block. Name blocks after the thing, not the
  provider type.
- Reference outputs of other resources rather than re-deriving their names.
- Use `for_each` over a map or set of objects rather than `count` whenever the
  instances are not interchangeable. `for_each` keys are stable across runs;
  `count` indices are not, so inserting an element in the middle of a list reshuffles
  and can destroy and recreate resources.
- Never build a `count` from a value that changes frequently.

```hcl
resource "aws_subnet" "private" {
  for_each = toset(var.availability_zone_ids)

  vpc_id            = aws_vpc.main.id
  availability_zone = each.value
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, index(var.availability_zone_ids, each.value))

  tags = merge(local.tags, {
    Name = "private-${each.value}"
    Tier = "private"
  })
}
```

### Explicit dependencies

Terraform infers dependencies from references. Use `depends_on` only for a real
ordering constraint that references cannot express. A `depends_on` on a whole
resource with many dependencies serializes unrelated changes and slows every plan.

## Modules and reuse

```
modules/
  network/
    main.tf
    variables.tf
    outputs.tf
    versions.tf
    README.md
```

```hcl
module "network" {
  source = "./modules/network"

  name_prefix        = local.name_prefix
  vpc_cidr           = "10.20.0.0/16"
  availability_zones = ["us-east-1a", "us-east-1b", "us-east-1c"]
  environment        = var.environment
}

module "platform" {
  source  = "app.terraform.io/acme/platform/aws"   # private registry module
  version = "~> 3.2"

  region              = var.region
  environment         = var.environment
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  tags                = local.tags
}
```

Module rules:

- Declare `required_version` and `required_providers` in every module, including
  child modules.
- Never `cd` or `apply` inside a module directory. Terraform applies from the root.
- Pin registry modules with `version =` (a range, never "latest").
- Prefer the registry over a `git::` URL for tagged versions; the registry gives
  integrity guarantees and discoverability.
- Write the module README and its variable and output contract. A module with
  undocumented inputs becomes a black box nobody is willing to delete.
- Keep providers out of modules where you can. A module that takes a provider
  configuration cannot be reused across accounts.

## Variables, locals, outputs, and sensitivity

```hcl
variable "instance_count" {
  type = number
  validation {
    condition     = var.instance_count >= 2 && var.instance_count % 2 == 0
    error_message = "instance_count must be an even number of at least 2."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}

output "db_endpoint" {
  value     = aws_db_instance.main.endpoint
  sensitive = true
}

output "subnet_ids" {
  value = [for s in aws_subnet.private : s.id]
}
```

- `sensitive = true` redacts a value from CLI output. It does **not** remove it from
  state, from a JSON plan, or from a log line someone prints from a variable.
  Handle sensitive values accordingly.
- Use `validation` blocks to encode constraints the type system cannot express.
  They run at plan time and produce a readable message.
- Prefer `locals` for computed names over duplicating expressions.

## Workspaces

```bash
terraform workspace list
terraform workspace new staging
terraform workspace select prod
terraform workspace show
terraform state show -workspace=staging aws_s3_bucket.artifacts
```

Each workspace has its own state, but all workspaces typically share one backend
configuration. Workspaces are convenient for small separations; they are not a
substitution for separate state, separate credentials, or separate backends. Many
teams get better isolation and clearer access control from one state per environment
with per-environment backend configuration.

## Drift detection

Drift is reality moving without Terraform knowing: a console edit, an autoscaler, a
policy.

```bash
# Show only what drift caused, without applying configuration changes
terraform plan -refresh-only

# Continuous detection, writing a report on a schedule
terraform plan -refresh-only -out=drift.tfplan
terraform show -json drift.tfplan
```

Handle drift by one of three decisions:

1. **Accept it** and reconcile config to match, if the change is legitimate.
2. **Revert it** with `terraform apply`, if the change is forbidden.
3. **Ignore it**, by importing the resource into state so it stops looking like
   drift. This is the right answer for resources something else legitimately owns
   (a managed database's engine defaults, an autoscaler).

Deciding which is which, per resource, is the actual work. A resource list nobody
reviews produces alert fatigue.

## Safe change review

- Never run `apply` directly against production from a laptop. Use the pipeline with
  a stored plan artifact and a protected environment.
- Set the plan output as a required CI artifact for the review.
- For high-blast-radius resources, add `prevent_destroy` lifecycle rules so a plan
  that would destroy them fails loudly:

```hcl
resource "aws_db_instance" "main" {
  # ...

  lifecycle {
    prevent_destroy = true
    ignore_changes = [tags["LastReviewed"]]  # deliberate, documented exceptions only
  }
}
```

`ignore_changes` is a permanent exception to drift detection. Every use is a decision
someone will forget; keep a list and review it.

- Break large changes into a sequence of small, reversible ones: add the new thing
  alongside the old, migrate, switch traffic, then delete the old.

## Policy as code

```hcl
# In CI, evaluate policy against the JSON plan
terraform show -json tfplan > tfplan.json
conftest test tfplan.json -p policy/
```

Common open-source options: Open Policy Agent with Conftest, tfsec, Checkov,
Terrascan, and Trivy configuration scanning. With OpenTofu and Terraform, policy can
also be evaluated inside the plan for well-supported backends, which fails the run
rather than reporting afterward.

Policies worth having on day one: no unencrypted storage, no public buckets or
databases, no wildcard IAM actions, every resource tagged with owner and
environment, logging enabled on data stores, and no ingress from `0.0.0.0/0` on
administrative ports.

## Choosing a tool

| Tool | Model | Best fit | Watch out for |
|---|---|---|---|
| Terraform | Declarative HCL, state file | Broad multi-cloud provider coverage, stable module ecosystem | State management, provider version churn |
| OpenTofu | Fork of Terraform, same HCL and CLI model | Open governance, Linux Foundation project | Provider compatibility is strong but verify for any plugin you rely on |
| Pulumi | Programs in Go, TypeScript, Python, .NET and others | Complex logic, real programming language, tests and refactoring | Varies by language; package quality differs |
| CloudFormation | Native AWS, declarative YAML or JSON | Single cloud, no separate toolchain | Verbose; template limits; drift on out-of-band changes |
| Bicep | Declarative, compiled to Azure ARM templates | Azure-native, strong intellisense, no JSON | Azure only |
| Ansible | Imperative, no state | Config management, one-off tasks, existing fleets | Not a strong fit for creating cloud infrastructure with drift correction |
| Crossplane | Kubernetes CRDs managing cloud resources | GitOps for cloud, reconciler model | Requires a cluster and a controller |

Decision heuristics:

- Multi-cloud or provider-neutral: Terraform or OpenTofu.
- Deeply conditional logic that would be unreadable in HCL: Pulumi.
- Single-cloud, want the vendor's native tooling and support path: CloudFormation
  or Bicep.
- Configuration management of servers you already have: Ansible.

OpenTofu is a drop-in for the vast majority of Terraform configurations; it forked
after the license change and retains the CLI and HCL model. Verify provider and
module availability for your specific use case in the OpenTofu registry.

## Checklist

- State is remote, encrypted, versioned, and locked.
- `terraform plan -out` to a file; `terraform apply` that same file.
- Every plan is read for "will be destroyed" and "forces replacement".
- `terraform fmt -check` and `terraform validate` run in CI.
- Providers pinned with pessimistic version constraints.
- Modules pinned to versions; child modules declare their own requirements.
- `for_each` used instead of `count` for anything that will grow.
- `prevent_destroy` on databases, state stores, and other irreplaceable resources.
- `sensitive` used where it applies, with the reminder that state is plain text.
- Policy checks run against the JSON plan and actually block.
- Separate credentials per environment; the production apply role cannot be
  assumed from a laptop.
- Drift reviewed on a schedule with a documented accept/revert/import decision for
  each finding.
- State files are gitignored.

## Common mistakes

**Symptom: `Error: Failed to get existing workspaces: S3 bucket does not exist`.**
Cause: backend resources created by hand, or created by another workspace's
configuration. Fix: bootstrap the backend once outside the normal workflow, or use
per-workspace backend configuration with `init -backend-config`.

**Symptom: `Error acquiring the state lock`.**
Cause: another apply is running, or a previous run was killed mid-apply. Fix: wait,
or confirm no process is running and use `terraform force-unlock` with the lock ID
from the error message.

**Symptom: the plan wants to destroy and recreate a resource that did not change.**
Cause: an attribute is marked as changing when set (ForceNew) and is being
recomputed each plan, often a timestamp, a random value, or an argument the provider
normalizes differently. Fix: move genuinely stable values into `tags` (ignored by
many resources) or into a `locals` value derived from immutable data, not from a
`timestamp()` call.

**Symptom: resource IDs renumbered after adding one item to a list.**
Cause: `count` on a list that reorders. Fix: convert to `for_each` keyed by a stable
identifier.

**Symptom: `terraform apply` fails partway and leaves a messy state.**
Cause: a dependency ordered wrong, or a resource created outside Terraform that the
configuration then tries to adopt. Fix: import existing resources, and address
ordering with explicit references (not `depends_on` on unrelated resources).

**Symptom: sensitive value printed in a log despite `sensitive = true`.**
Cause: someone wrote `output` referencing it without `sensitive`, or used a `local`
of the value inside a non-sensitive expression. Fix: trace the reference path; note
that `sensitive = true` only redacts the CLI rendering, not the value in state.

**Symptom: `terraform plan` takes minutes.**
Cause: refreshing many remote objects, or a provider doing heavy API calls. Fix:
`-refresh-only` for read-only checks, `-target` for surgical lookups only (with the
understanding that it produces partial state and is not for routine use), and reduce
the number of resources per state.

**Symptom: `terraform init` asks for credentials every time.**
Cause: backend or provider authentication is not using a shared credentials file or
workload identity. Fix: configure the CLI once, or rely on the environment identity
in CI.

**Symptom: `apply` says the resource already exists.**
Cause: the resource was created outside Terraform and is not in state. Fix: use
`terraform import` (or the declarative import block in newer versions) before
planning further changes.

## Anti-patterns

- Applying from a laptop against production with long-lived admin credentials.
- Storing state in a git repository in plain text.
- `count` on collections that grow or reorder.
- One giant state for an entire organization. Blast radius on `apply`, one lock, one
  slow plan, one merge queue.
- `terraform destroy` run to "clean up" instead of targeted `state rm` plus a real
  removal procedure.
- `ignore_changes = [all]` used to silence a plan, which permanently disables drift
  detection for that resource.
- Copy-pasting the same 60-line security group across regions instead of a module.
- `depends_on` sprinkled on every resource to fix ordering, serializing everything.
- Secrets in `terraform.tfvars` committed to the repository, or in variables without
  `sensitive = true`.
- Unpinned module sources pointing at a moving branch.
- Auto-approving applies in CI with no human review of the plan artifact.
- Treating drift alerts as noise by running `-refresh=false` everywhere.

## References

- Terraform documentation: https://developer.hashicorp.com/terraform/docs
- Language reference (HCL): https://developer.hashicorp.com/terraform/language
- State: https://developer.hashicorp.com/terraform/language/state
- Backends: https://developer.hashicorp.com/terraform/language/backend
- Command reference (`plan`, `apply`, `import`, `force-unlock`): https://developer.hashicorp.com/terraform/cli/commands
- Provider requirements and lock file: https://developer.hashicorp.com/terraform/language/dependency-management
- Module development: https://developer.hashicorp.com/terraform/language/modules
- `count` and `for_each` reference: https://developer.hashicorp.com/terraform/language/meta-argument/for_each
- Import: https://developer.hashicorp.com/terraform/language/import
- OpenTofu: https://opentofu.org/docs/
- Pulumi: https://www.pulumi.com/docs/
- CloudFormation: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/
- Bicep: https://learn.microsoft.com/azure/azure-resource-manager/bicep/
- Ansible: https://docs.ansible.com/ansible/latest/
- Crossplane: https://docs.crossplane.io/latest/
- Conftest / Open Policy Agent: https://www.conftest.dev/
- Checkov: https://www.checkov.io/
- AWS IAM policy reference for writing policies: https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies.html

Verify before relying on this: Terraform and its providers change frequently.
Confirm CLI flags with `terraform <command> -help` and provider-specific arguments
with the provider's own registry documentation for the version pinned in
`required_providers`.