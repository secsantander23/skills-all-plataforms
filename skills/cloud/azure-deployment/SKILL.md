---
name: Azure Deployment
description: Covers Microsoft Azure application deployment including management group, subscription and resource group hierarchy, Azure RBAC and managed identities, choosing between App Service, Container Apps, AKS and Functions, Blob Storage tiers and lifecycle, Azure SQL, Key Vault, Azure Monitor, and networking basics. Use when the user asks about deploying to Azure, writing Azure RBAC or Bicep, choosing an Azure compute service, configuring Blob storage or Azure SQL, using Key Vault or managed identities, or reviewing an Azure architecture.
metadata:
  opencode/autoinvoke: true
---

# Azure Deployment

Azure's organizing ideas are the **hierarchy** (management group, subscription,
resource group, resource) and **identity** (Microsoft Entra ID principals bound to
Azure RBAC roles at a scope). As on the other clouds, nail those two and the service
choices follow.

Service availability, quotas, and pricing change. Read this as a decision framework
and verify current details in the linked Microsoft Learn documentation.

## When to use this

- Deploying an application, container, or function to Azure.
- Organizing management groups, subscriptions, and resource groups.
- Writing Azure RBAC role assignments or custom role definitions.
- Using managed identities instead of secrets.
- Choosing between App Service, Container Apps, AKS, Functions, and VMs.
- Storing data in Blob Storage with tiers, lifecycle management, and immutability.
- Provisioning Azure SQL and understanding its availability options.
- Using Key Vault for secrets, keys, and certificates.
- Setting up Log Analytics, Application Insights, alerts, and action groups.
- Designing VNets, NSGs, private endpoints, and ingress.
- Estimating cost or reviewing an Azure architecture.

## Quick start

Container Apps with a managed identity, Key Vault secret, and private data tier.

```bash
az login
az account list -o table
az account set --subscription "Acme Prod"
az group create --name acme-prod-rg --location eastus2

# Managed identity: no credential file, no secret, rotates automatically
az identity create --name acme-api-id --resource-group acme-prod-rg --location eastus2

az keyvault create --name acme-prod-kv --resource-group acme-prod-rg \
  --location eastus2 --enable-rbac-authorization true \
  --enable-soft-delete true --enable-purge-protection true

az role assignment create \
  --assignee-object-id "$(az identity show -n acme-api-id -g acme-prod-rg --query principalId -o tsv)" \
  --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets User" \
  --scope "/subscriptions/<sub-id>/resourceGroups/acme-prod-rg/providers/Microsoft.KeyVault/vaults/acme-prod-kv"
```

```bicep
// main.bicep
param location string = resourceGroup().location
param image string

resource env 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: 'acme-env'
  location: location
  sku: {
    name: 'ConsumptionOnly'
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'acme-api-id'
  location: location
}

resource app 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'acme-api'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: 'https://acme-prod-kv.vault.azure.net/secrets/database-url'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: {
            cpu: json('1.0')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8080 }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/readyz', port: 8080 }
              periodSeconds: 5
              failureThreshold: 3
            }
          ]
          env: [
            { name: 'NODE_ENV', value: 'production' }
          ]
        }
      ]
    }
  }
}
```

```bash
az deployment group create \
  --resource-group acme-prod-rg \
  --template-file main.bicep \
  --parameters image=acmeacr.azurecr.io/acme-api:1.5.0
```

## Hierarchy

```
Management group (tenant)
├── Subscription: acme-platform     (landing zones, policy, shared services)
├── Subscription: acme-prod        (one per environment, per region if needed)
│   └── Resource group: acme-prod-app-eus
│       └── App Service, Container Apps, Key Vault
└── Subscription: acme-dev
    └── Resource group: acme-dev-app-eus
```

| Level | Holds | Notes |
|---|---|---|
| Management group | Subscriptions | Where Azure Policy, budget, and RBAC at scale belong |
| Subscription | Resource groups | The billing boundary and the quota boundary |
| Resource group | Resources | Lifecycle and access boundary; name it for environment plus app plus region |
| Resource | One service object | Can carry its own role assignments |

Practical guidance:

- **One subscription per production environment at minimum.** A subscription is the
  hard quota and hard billing boundary. Grouping many environments into one
  subscription means one quota pool and one blast radius.
- **Resource groups per application plus environment.** This gives a natural RBAC
  scope and a natural place to apply policy assignments and tags.
- **Management groups for landing zones.** Azure Policy assignments and role
  definitions defined at the management group level apply to everything beneath,
  which is how you enforce guardrails at scale.

```bash
az account list -o table
az account set --subscription "Acme Prod"
az group list -o table
az group create --name acme-prod-app-eus --location eastus2 \
  --tags environment=prod application=acme-api region=eastus2 owner=platform
az tag list
az policy state list
```

**Always pass `--subscription` and `--resource-group` explicitly.** The Azure CLI's
active context changing silently is a common source of deploying to the wrong place.

## Azure RBAC and managed identities

### Roles versus access policies

- **Azure RBAC** assigns a role to a principal at a scope. It is the model to use.
- **Access policies** are the older Key Vault data-plane model. If you enable
  `--enable-rbac-authorization`, do not mix in access policies for the same vault;
  pick one model.

### Built-in, custom, and scope

```bash
# What a built-in role allows
az role definition list --name "Storage Blob Data Reader" -o table

# Assign at the narrowest useful scope
az role assignment create \
  --assignee acme-api-id \
  --role "Storage Blob Data Reader" \
  --scope "/subscriptions/<sub>/resourceGroups/acme-prod-app-eus/providers/Microsoft.Storage/storageAccounts/acmedata"

# Create a custom role
az role definition create \
  --name "Acme Key Vault Config Reader" \
  --description "Read app configuration secrets only" \
  --permissions "[{
    'actions': [
      'Microsoft.KeyVault/vaults/secrets/getSecretVersions/listSecretVersions'
    ],
    'notActions': [],
    'dataActions': [],
    'notDataActions': [],
    'noDataActions': []
  }]" \
  --assignableScopes "['/subscriptions/<sub>']"

az role assignment list --assignee <object-id> --all --include-inherited -o table
```

Built-in roles to know: `Owner`, `Contributor`, `Reader` (broad; avoid for
workloads), `Key Vault Secrets User`, `Key Vault Certificate User`, `Storage Blob
Data Contributor`, `Storage Blob Data Reader`, `Monitoring Reader`,
`AcrPull` (for container registry), and `Azure Service Bus Data Sender`/Receiver.

### Managed identities

| Type | Lifecycle | Attached to |
|---|---|---|
| System-assigned | Created and deleted with the resource | The resource itself |
| User-assigned | Independent lifecycle, reusable | One or many resources |

Use user-assigned identities for anything shared or anything that must survive
resource replacement. Their principal ID is stable, which makes role assignments and
audit logs meaningful.

```bash
# User-assigned identity
az identity create --name acme-api-id -g acme-prod-app-eus -l eastus2
az identity show -n acme-api-id -g acme-prod-app-eus --query principalId -o tsv

# System-assigned identity on an existing resource
az resource update --ids "<resource-id>" --set "identity.type=SystemAssigned"

# Check what credentials the identity can reach
IDENTITY_URL="http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/"
curl -s -H "Metadata: true" "$IDENTITY_URL"
```

Token caching is real: the token endpoint returns a token with a lifetime and a client
caching it. Long-lived processes need a library that refreshes, and must not cache a
token past its expiry.

### App configuration pattern

```bash
# Key Vault reference as a setting (resolved at runtime by the platform)
az webapp config appsettings set \
  --name acme-api --resource-group acme-prod-app-eus \
  --settings DATABASE_URL="@Microsoft.KeyVault(SecretUri=https://acme-prod-kv.vault.azure.net/secrets/database-url)"

# Managed identity first, then federated credential for a workload
az account get-access-token --resource-type arm --query access_token -o tsv
```

Key Vault references mean the secret value never appears in app settings or in the
deployment pipeline. That is a large improvement over putting a connection string in
a configuration file.

## Compute selection

| Service | You manage | Scale to zero | Best fit |
|---|---|---|---|
| App Service | App, plan, deployment slots | No (Basic and above) | Web apps and APIs, any stack, slots for blue/green |
| Container Apps | Container, revision, rules | Yes | Containers without a cluster, event-driven, internal services |
| AKS | Cluster, node pools, upgrades | Yes (cluster autoscaler) | You need Kubernetes APIs, custom scheduling, or portability |
| Functions | Function, triggers, bindings | Yes | Event handlers and glue logic |
| Virtual Machines | The OS entirely | No | You install and operate the software yourself |

Decision path:

1. Container image, HTTP-triggered, no cluster to operate: **Container Apps**.
2. Any language with a first-party runtime (or a container), and you want deployment
   slots: **App Service**.
3. Kubernetes required, or the team already operates it: **AKS**.
4. Small event handlers with many triggers: **Functions**.
5. Software you manage end to end, or Windows workloads: **Virtual Machines** or AKS.

Container Apps specifics worth knowing: revisions are immutable and traffic-weighted;
KEDA drives scaling from a large set of scalers; the Dapr integration is optional;
and jobs are a first-class workload type. App Service specifics: deployment slots
enable near-instant swap for blue/green, and `always on` keeps the worker warm.

App Service plan tiers matter operationally: consumption-style plans scale out and
scale in, while a dedicated plan keeps capacity warm at a fixed cost. Verify current
tier names and features in the Azure App Service plans documentation.

## Blob Storage

```bash
az storage account create \
  --name acmedata --resource-group acme-prod-app-eus \
  --location eastus2 \
  --sku Standard_ZRS \
  --kind StorageV2 \
  --encryption-services blob \
  --https-only true \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false \
  --default-action Deny \
  --resource-group acme-prod-app-eus

az storage account blob-service-properties update \
  --account-name acmedata --resource-group acme-prod-app-eus \
  --delete-retention-policy-days 14 \
  --versioning true
```

Access tiers:

| Tier | Shape |
|---|---|
| Hot | Frequent access, lowest latency |
| Cool | Infrequently accessed, still instant |
| Cold | Rarely accessed, higher retrieval latency |
| Archive | Very rarely accessed, rehydration takes hours, minimum retention |

Blobs are online by default; a blob in a cool or cold tier is still readable but
with different latency, and an archive blob must be rehydrated before read.

Redundancy options: locally redundant, geo-zone-redundant, geo-redundant,
geo-redundant with access tiers. Verify current option availability in your region.

```bicep
// Lifecycle management policy
resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-01-01' = {
  name: 'default'
  parent: sa
  properties: {
    policy: {
      rules: [
        {
          name: 'tier-cold-data'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['uploads/']
            }
            actions: {
              baseBlob: {
                tierToCool:   { daysAfterModificationGreaterThan: 30 }
                tierToArchive:{ daysAfterCreationGreaterThan: 180 }
                delete:       { daysAfterCreationGreaterThan: 2555 }
              }
              versions: {
                delete: { daysAfterCreationGreaterThan: 90 }
              }
            }
          }
        }
      ]
    }
  }
}
```

Also set: soft delete and versioning (they multiply storage), container delete
retention, and immutability policies for compliance workloads. Public blob access
must be disabled at the account level, not only per container.

## Azure SQL

```bash
az sql server create \
  --name acme-sql-prod --resource-group acme-prod-app-eus \
  --location eastus2 \
  --admin-username sqladmin \
  --admin-password "$SQL_ADMIN_PASSWORD"

az sql server firewall-rule create \
  --server acme-sql-prod --name deny-all \
  --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0

az sql db create \
  --resource-group acme-prod-app-eus --server acme-sql-prod \
  --name acmedb \
  --edition Standard \
  --tier GeneralPurpose \
  --capacity 2 \
  --min-capacity 0.5 \
  --max-capacity 4 \
  --zone-redundant true \
  --backup-storage-redundancy Geo

az sql db replica create \
  --resource-group acme-prod-app-eus --server acme-sql-prod \
  --name acmedb --replica-server-name acme-sql-geo

az sql db show --name acmedb --server acme-sql-prod \
  --query '{status:status,zoneRedundant:zoneRedundant,failoverState:creationSource}'
```

- **Azure SQL Database** is a single database; **Managed Instance** gives a wider
  feature surface closer to SQL Server, at a higher starting cost.
- **Zone-redundant** spreads the compute and storage across zones in one region for
  high availability. It is not a backup; verify the region supports it.
- **Failover groups** provide automatic failover between a primary and a geo-secondary.
  Failover loses in-flight transactions and is not instantaneous. Know which one you
  would use and how connections recover.
- Use **Entra ID authentication** rather than SQL logins where possible, with the
  `Microsoft Entra authentication` administrator set on the server.
- Use **private endpoints** with a virtual network rule rather than a firewall rule
  where the platform supports it.
- Enable **auditing** to Log Analytics, and configure **transparent data encryption**
  (on by default; confirm the configuration in your region).

```bash
az sql db audit-policy show --server acme-sql-prod --database acmedb -o table
az sql failover-group list --server acme-sql-prod -o table
az sql db replica list --server acme-sql-prod -o table
```

## Key Vault

```bash
az keyvault set-policy \
  --name acme-prod-kv --resource-group acme-prod-app-eus \
  --secret-permissions get list

az keyvault secret set \
  --vault-name acme-prod-kv \
  --name database-url \
  --value "postgres://app:...@acmedb.postgres.database.azure.com/app" \
  --expires "$(date -u -d '+90 days' +%Y-%m-%dT%H:%MZ)"

az keyvault secret list --vault-name acme-prod-kv -o table
az keyvault secret show --vault-name acme-prod-kv --name database-url --query attributes.expires
az keyvault secret set --vault-name acme-prod-kv --name database-url --value "new" --expires "..."
```

Design points:

- Separate vaults per environment so a production vault's policy cannot be
  relaxed by a staging change.
- Private endpoints on the vault for production, with network rules denying public
  access.
- Soft delete on (default) and purge protection on for production. Purge protection
  makes deletion permanent by design; enable it deliberately.
- Rotation: Key Vault does not rotate application secrets on its own. Rotation is
  the application's job, using a new-version-then-swap pattern, or a managed key
  rotation for keys.
- Managed identity access plus RBAC, never connection strings with a key in them.
- Access the vault with `DefaultAzureCredential` in code so the identity is
  discovered from the environment rather than configured.

```bash
az keyvault backup list --vault-name acme-prod-kv -o table
az keyvault recovery-service list --vault-name acme-prod-kv -o table
az keyvault private-endpoint list --vault-name acme-prod-kv -o table
```

## Azure Monitor

```bash
# Log Analytics workspace, the shared query and storage layer
az monitor log-analytics workspace create \
  --workspace-name acme-logs-eastus2 \
  --resource-group acme-prod-app-eus --location eastus2 \
  --retention-in-days 30 \
  --daily-quota-gb 10

# Application Insights, linked to the workspace for one query surface
az monitor app-insights component create \
  --app acme-api-insights --resource-group acme-prod-app-eus \
  --location eastus2 \
  --application-type web \
  --workspace-resource-id "<workspace-resource-id>"

# Action group: where alerts go, and who is on call
az monitor action-group create \
  --name acme-oncall --resource-group acme-prod-app-eus \
  --short-name acme \
  --email oncall@acme.example \
  --email-create-or-update devops@acme.example \
  --webhook-url https://hooks.slack.com/services/REPLACE/ME \
  --severity 2

# Metric alert
az monitor metrics alert create \
  --name acme-api-5xx \
  --resource-group acme-prod-app-eus \
  --condition "count PT1M dimensions('[Microsoft.Web/sites].location') static eq 0" \
  --description "5xx responses above threshold"
```

Kusto is the query language, shared by Log Analytics, Application Insights, and
Activity Log:

```kusto
ContainerLog
| where TimeGenerated > ago(1h)
| project TimeGenerated, Computer, LogLevel, Message
| where LogLevel in ("Error", "Critical")
| summarize Errors=count() by Computer, bin(TimeGenerated, 5m)
| order by Errors desc
```

```kusto
// Availability of the health endpoint, the SLI
requests
| where timestamp > ago(1d)
| where operation_Name == "GET /healthz"
| summarize Total=count(), Failed=countif(success == false) by bin(timestamp, 5m)
| extend Availability = 100.0 * (Total - Failed) / Total
| summarize Availability=min(Availability)
```

Always enable diagnostic settings on resources that do not send logs by default:
App Service, AKS, Application Gateway, Key Vault, and load balancers.

```bash
az monitor diagnostic-settings create \
  --name acme-api-diag --resource "<resource-id>" \
  --workspace "<workspace-resource-id>" \
  --metrics '[{"category":"AllMetrics","enabled":true}]' \
  --logs '[{"category":"AppTraces","enabled":true}]'
```

An operational gap worth checking: alert on the alerting pipeline itself. A silent
alert path is indistinguishable from a healthy system.

## Networking

```bash
az network vnet create --name acme-vnet --resource-group acme-prod-app-eus \
  --address-prefixes 10.20.0.0/16 --subnet-name default --subnet-prefixes 10.20.0.0/24

az network vnet subnet create --resource-group acme-prod-app-eus \
  --vnet-name acme-vnet --name app --address-prefixes 10.20.1.0/24 \
  --delegations Microsoft.Web/serverFarms

az network nsg create --name acme-nsg --resource-group acme-prod-app-eus
az network nsg rule create --name allow-https --nsg-name acme-nsg \
  --resource-group acme-prod-app-eus --priority 100 \
  --source-address-prefix Internet --source-port-range '*' \
  --destination-address-prefix VirtualNetwork --destination-port-ranges 443 \
  --access Allow --protocol Tcp --direction Inbound

az network private-endpoint create --name acme-sql-pe \
  --resource-group acme-prod-app-eus \
  --subnet app --vnet-name acme-vnet \
  --private-link-resource-id "<sql-server-resource-id>" \
  --group-ids sqlServer --private-connection-resource-id acme-sql-pe-conn

az network private-dns zone create --name privateregion.database.windows.net \
  --resource-group acme-prod-net

az network bastion create --name acme-bastion --resource-group acme-prod-net \
  --public-ip-address acme-bastion-pip \
  --vnet-name acme-vnet --subnet app

az network watcher test-ip-flow \
  --resource-group acme-prod-net --vm acme-vm-01 \
  --direction Inbound --protocol TCP \
  --local 10.20.1.10:8080 --remote 203.0.113.10:*
```

| Concern | Options |
|---|---|
| Ingress | Application Gateway (regional, HTTP/7), Front Door (global, edge), Traffic Manager (DNS-based routing) |
| East-west | VNet peering, Azure Firewall, Network Security Groups |
| On-premises | VPN gateway, ExpressRoute (long lead time) |
| Private service access | Private endpoints with private DNS zones |
| Operations | Bastion, Network Watcher (connection troubleshoot, flow logs, packet capture) |

Private endpoints need a private DNS zone, and the application must resolve the
service name correctly. The most common cause of "private endpoint works but the
application times out" is a missing or wrong private DNS zone record.

## Cost awareness

Levers without quoting prices:

```bash
az consumption usage list \
  --start-date 2026-01-01 --end-date 2026-02-01 \
  --query "[].{name:properties.meterName,usage:properties.usageQuantity}" -o table

az consumption budget create \
  --budget-name acme-prod-monthly --resource-group acme-prod-app-eus \
  --time-grain Monthly --time-window "2026-01-01/2026-02-01" \
  --amount 5000 --category Cost \
  --notification-threshold 80 \
  --notification-contact "@acme.example"

az resource tag --resource-id "<id>" --tags environment=prod application=acme-api

az costmanagement export create \
  --name acme-daily-export \
  --scope "/subscriptions/<sub>" \
  --type ActualCost \
  --format Csv \
  --storage-account-id "<storage-account-id>"
```

Levers:

1. **Tags on every resource**, enforced in IaC or by Azure Policy (deny on missing
   tags). Cost allocation cannot be retrofitted.
2. **Right-size App Service plans, Container Apps concurrency, SQL tiers** from
   measured utilization rather than defaults.
3. **Reservations and savings plans** for stable compute, databases, and storage.
   Verify current program names and coverage.
4. **Zone-redundant and geo-redundant configurations are deliberate choices**, not
   defaults. Each adds cost and a specific availability guarantee.
5. **Log Analytics retention and daily quota**, plus diagnostic settings scope. Log
   ingestion is frequently the fastest-growing line item after an observability
   rollout.
6. **Idle resources**: unused public IPs, unattached disks, forgotten deployments
   slots, orphaned container registries.
7. **Bandwidth egress** between regions and out of Azure, which is metered and often
   the largest surprise.

Azure Hybrid Benefit applies to eligible Windows and SQL workloads with qualifying
licensing; verify eligibility before planning around it.

## Checklist

- Subscription per production environment; resource groups per app plus environment
  plus region.
- Azure Policy assignments at the management group for guardrails.
- Every workload uses a managed identity; no connection strings with keys.
- Role assignments at the narrowest scope, from a documented catalog of roles.
- Key Vault with RBAC authorization, private endpoint in production, purge
  protection, and rotation performed by the application.
- Storage account with HTTPS only, minimum TLS, public blob access disabled,
  lifecycle management, and soft delete.
- Azure SQL with Entra ID authentication, private endpoint, zone redundancy decided
  deliberately, auditing enabled, and backups verified by restore.
- Diagnostic settings enabled on every resource that does not log by default.
- Action groups route somewhere a human will see, and the alerting path itself is
  monitored.
- Tags required at creation and validated in the pipeline.
- `az account set` is never relied upon implicitly; every command names its
  subscription and resource group.
- Rollback path written down for each deployment type.

## Common mistakes

**Symptom: a resource was created in the wrong subscription or resource group.**
Cause: the CLI active context changed. Fix: `az account set` explicitly per command,
or use `--subscription` and `--resource-group` on every call; also check the
deployment scope, since group-scoped deployments inherit the currently selected
subscription.

**Symptom: `AuthorizationFailed` despite having a role.**
Cause: the role was assigned at a scope that does not cover the resource, the
assignment was created with the wrong principal type, or the change has not
propagated. Fix: check with
`az role assignment list --assignee <id> --all --include-inherited -o table`, and
confirm the resource ID in the message matches the scope.

**Symptom: managed identity cannot read a Key Vault secret.**
Cause: `--enable-rbac-authorization` is off (so data-plane access policies apply
instead of RBAC), or the identity lacks `Key Vault Secrets User`, or the vault's
network rules block the caller. Fix: enable RBAC authorization on the vault, grant
the data-plane role on the vault scope, and confirm private endpoint plus DNS if
networking is restricted.

**Symptom: private endpoint works but the application times out.**
Cause: the private DNS zone is missing or not linked to the VNet, so the
application resolves the public name. Fix: create the zone, link it to the VNet,
and verify the name resolves to the private address from inside the VNet.

**Symptom: `az deployment group create` fails with subscription errors.**
Cause: wrong tenant, wrong subscription, or the principal cannot create resources
at the scope. Fix: `az account show`, then `az role assignment list --scope ...` to
see who has what.

**Symptom: unexpected App Insights costs.**
Cause: verbose logging, high sampling volume, or an exception-heavy workload being
recorded in full. Fix: tune sampling, filter noisy dependencies, and scope
diagnostic settings.

**Symptom: Log Analytics stops ingesting.**
Cause: daily cap reached, or the workspace's retention plus ingestion quota was
exceeded. Fix: check the workspace quota and raise it deliberately, then fix the
noisy source.

## Anti-patterns

- Owner or Contributor assigned to a workload identity.
- Managed identity attached with no RBAC, or with a role discovered by trial and
  error instead of from the permission set the application actually needs.
- Storage keys, connection strings, or SAS tokens in application configuration or
  pipeline variables when a managed identity would work.
- Access policies enabled alongside RBAC authorization on the same vault.
- Public blob access enabled on any container.
- Firewall rules allowing `0.0.0.0` on `169.254.169.254` or 3389 in the name of
  "just for debugging". Use Bastion.
- Resource names or environments inferred from a globally selected CLI context.
- Manual portal changes in production with no matching infrastructure-as-code change.
- Alerts on infrastructure metrics rather than on user-visible symptoms.
- Zone-redundant or geo-redundant everywhere by default, without noting the cost or
  deciding whether the availability guarantee is needed.

## References

- Resource hierarchy and resource groups: https://learn.microsoft.com/azure/azure-resource-manager/management/resource-groups-for-azure-resources
- Management groups: https://learn.microsoft.com/azure/governance/management-groups
- Subscriptions: https://learn.microsoft.com/azure/azure-resource-manager/management/subscription-overview
- Azure RBAC: https://learn.microsoft.com/azure/role-based-access-control/
- Built-in roles: https://learn.microsoft.com/azure/role-based-access-control/built-in-roles
- Managed identities: https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/
- App Service: https://learn.microsoft.com/azure/app-service/
- Container Apps: https://learn.microsoft.com/azure/container-apps/
- AKS: https://learn.microsoft.com/azure/aks/
- Azure Functions: https://learn.microsoft.com/azure/azure-functions/
- Blob Storage tiers and lifecycle: https://learn.microsoft.com/azure/storage/blobs/access-tiers-overview, https://learn.microsoft.com/azure/storage/blobs/lifecycle-management-overview
- Blob immutability: https://learn.microsoft.com/azure/storage/blobs/immutable-storage-overview
- Azure SQL: https://learn.microsoft.com/azure/azure-sql/database/
- Key Vault: https://learn.microsoft.com/azure/key-vault/general/overview
- Azure Monitor and Log Analytics: https://learn.microsoft.com/azure/azure-monitor/
- Kusto query reference: https://learn.microsoft.com/kusto/query/
- Networking: https://learn.microsoft.com/azure/virtual-network/
- Private endpoints and DNS: https://learn.microsoft.com/azure/private-link/private-endpoint-overview
- Azure Policy: https://learn.microsoft.com/azure/governance/policy/overview
- Bicep: https://learn.microsoft.com/azure/azure-resource-manager/bicep/
- Cost management: https://learn.microsoft.com/azure/cost-management/billing/
- Azure CLI reference: https://learn.microsoft.com/cli/azure/

Verify before relying on this: Azure service availability, SKUs, quotas, and pricing
change frequently, and some resources are region-limited. Confirm current behavior
with `az <group> --help`, check the linked Learn documentation, and confirm the
`apiVersion` in a Bicep template against the resource provider's API reference before
deploying.