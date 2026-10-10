# Safe Change Management Workflow

The pipeline below is intentionally conservative. Each step exists because a real
failure mode has taught someone a lesson that this document now carries.

## Layout

```
infra-terraform/
  envs/dev/{main.tf,backend.hcl,outputs.tf}
  envs/staging/{main.tf,backend.hcl,outputs.tf}
  envs/prod/{main.tf,backend.hcl,outputs.tf}
  modules/{network,database,service}/
  policy/{terraform.rego,checkov.yaml}
  scripts/{plan.sh,apply.sh,import-drift.sh}
```

`scripts/plan.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

: "${TF_IN_AUTOMATION:?set TF_IN_AUTOMATION=1 in CI}"
: "${AWS_REGION:?AWS_REGION must be set}"

ENVIRONMENT="$1"          # dev | staging | prod
BRANCH="${GITHUB_REF_NAME:-local}"

cd "envs/${ENVIRONMENT}"

terraform fmt -check -recursive
terraform init -input=false -backend-config=backend.hcl
terraform validate

# Read-only credentials for planning. The plan role must not be able to mutate.
terraform plan \
  -input=false \
  -lock-timeout=5m \
  -out="tfplan-${BRANCH}.tfplan" \
  -var-file="terraform.tfvars"

terraform show -no-color "tfplan-${BRANCH}.tfplan" > "plan-${BRANCH}.txt"
terraform show -json     "tfplan-${BRANCH}.tfplan" > "plan-${BRANCH}.json"

# Fail the run on destructive changes unless explicitly approved.
if grep -qE '^  # .* will be destroyed' "plan-${BRANCH}.txt"; then
  echo "::error::Plan contains resource destruction. Review or set ALLOW_DESTROY=1 with a ticket reference."
  if [ "${ALLOW_DESTROY:-0}" != "1" ]; then
    exit 1
  fi
fi

conftest test "plan-${BRANCH}.json" -p ../../policy/ || exit 1
```

`scripts/apply.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="$1"
BRANCH="${GITHUB_REF_NAME:?}"
TICKET="${CHANGE_TICKET:?a change ticket reference is required}"

cd "envs/${ENVIRONMENT}"

# Apply the exact plan that was reviewed, not a fresh one.
terraform apply -input=false -lock-timeout=10m "tfplan-${BRANCH}.tfplan"

terraform output -json > "outputs-${BRANCH}.json"

# Post-apply verification
./scripts/smoke.sh "$ENVIRONMENT"

{
  echo "Applied $ENVIRONMENT from $BRANCH (ticket $TICKET)"
  echo "Plan SHA256: $(sha256sum "tfplan-${BRANCH}.tfplan" | cut -d' ' -f1)"
} >> /var/log/acme-infra-applies.log
```

## Ordering: why the steps are in this sequence

| Step | Failure it prevents |
|---|---|
| `fmt -check` | Unreviewable diffs from whitespace noise |
| `validate` | Wasting a plan cycle on a structural error |
| Plan with read-only credentials | A plan run that mutates anything |
| Save plan and JSON | Reviewing a different plan than the one applied |
| Grep for destruction | Accidentally deleting a database |
| Policy gate | Merging an unencrypted bucket or an open security group |
| Human approval on the environment | Unreviewed production change |
| `apply` the saved plan | Drift between review and apply |
| Post-apply smoke test | A green pipeline over a broken service |
| Audit line | Being unable to answer "who changed this, when" |

## Import and rename without downtime

Renaming a resource in configuration without `terraform state mv` causes a
destroy/create plan, which means downtime and possibly data loss.

```bash
# 1. Move in state first (no infrastructure change)
terraform state mv 'aws_db_instance.main' 'module.database.aws_rds_cluster.primary'
terraform state mv 'module.network.aws_subnet.public[0]' \
                 'module.network.aws_subnet.public["us-east-1a"]'

# 2. Update the configuration to match the new address
# 3. Confirm the plan is now empty for that resource
terraform plan
```

For adopting existing infrastructure, follow the import procedure in
`module-design.md`: describe reality, import, confirm an empty plan, then change.

## Emergency change procedure

When production is on fire and the normal path is too slow:

1. Get the change done through the fastest safe mechanism: the cloud console is
   acceptable **if** the change is already codified and someone is writing the
   matching pull request during the incident.
2. Record the exact actions: who, what, when, and the identifiers.
3. Reconcile with code immediately after: apply the reviewed configuration, or make
   the emergency change in configuration first and apply it the moment it is safe.
4. Mark the drift so the next drift review does not re-discover it.

The rule that matters: an emergency change without a follow-up code change is
permanent drift with a memory. It will be reverted by the next unrelated apply.

## Monthly review

- State versions retained for at least a quarter.
- Provider version bumps, each one planned in a scratch workspace first.
- Module versions and their changelogs.
- `terraform plan -refresh-only` across every environment; triage each finding with
  the accept/revert/import decision.
- IAM policies and roles that have not been used in a defined period.
- Review every `ignore_changes` and `prevent_destroy` in the repository; delete the
  ones that no longer mean anything.
- Confirm the state backend still has encryption and versioning enabled, and that
  access to it is limited to the pipeline identity.

## What not to automate

Do not automate approval of destructive changes, auto-remediation that runs `apply`
against production from an alert, or retries that re-run `apply` after a partial
failure without first inspecting state. Partial failures need a human to look at the
state, not a retry loop.