# Supply chain and dependency review

## The threat in one sentence

Someone publishes a new version of a package you depend on, or you install a package
whose name is one character away from a popular one, and code you did not review runs on
your machine and in your build.

## What a lockfile does and does not do

A lockfile with integrity hashes pins exactly what you install and detects tampering with
the tarball. It does not tell you whether the pinned version is safe. A malicious version
that gets merged once becomes a valid, hash-verified, permanently locked entry.

This is why the lockfile is necessary and not sufficient.

## Install scripts

A dependency may run code during installation through a lifecycle script. That is code
execution with your privileges, on every machine and in CI, before you run a single test.

Modern managers can restrict or gate build scripts. pnpm has settings for this in
`pnpm-workspace.yaml`; npm has an `ignore-scripts` option. The exact setting names change
between versions, so read the manager's build-script settings before relying on a specific
key.

Practical approach:

1. Default to not running build scripts, then allow-list the specific packages that
   genuinely need one.
2. A package that needs `postinstall` to compile is legitimate for some ecosystems, such
   as packages with native bindings. Read what the script does before allowing it.
3. A package whose install script downloads a binary from a URL at runtime deserves
   serious scrutiny. There are legitimate uses and there are stolen-credential campaigns.

## Dependency review

Questions to answer when adding or reviewing a dependency:

| Question | Why it matters |
| --- | --- |
| How many transitive dependencies does it bring? | Blast radius of a compromise |
| When was the last release, and by whom? | Typosquats and abandoned packages differ |
| Does the repository URL match what you expect? | Typosquatting defence |
| Does it need install scripts, or native code? | Execution at install time |
| Does it make network calls at runtime? | Data exfiltration path |
| Could we write the 50 lines ourselves? | Sometimes yes |
| Is it already in the tree transitively? | Use what is there instead of adding |

## Transitive surface

```bash
# Direct dependencies.
pnpm list --depth 0

# One level down.
pnpm list --depth 1

# Why is this package installed at all?
pnpm why <package>
```

A package that pulls 400 transitive dependencies for one function is a decision, and it
should be a deliberate one. Check the total before adding, not after.

## Auditing

```bash
pnpm audit
npm audit
yarn npm audit
```

Configure the audit command to fail CI on a severity threshold that blocks releases, at
least for production dependencies. Note that:

- An audit reflects known advisories only. Absence of findings is not evidence of safety.
- Fixing can be a major-version upgrade. Read the changelog, run the tests, and land the
  upgrade separately from feature work.
- A `force` flag applied to every finding will eventually break the build. Triage instead.

## Automated updates

Use a tool that opens pull requests rather than a bulk upgrade command. Benefits: the
diff is reviewable, CI runs, and the update is revertible.

Rules:

- One dependency per pull request, or one small group with a single reason.
- Never merge an upgrade and a feature in the same change. You want to be able to bisect.
- Group patch bumps separately from minor and major so a noisy upgrade does not train the
  team to click through.
- Dependabot and Renovate are the common choices; both let you scope to production
  dependencies and schedule noisy updates separately.

## Pinning and the source registry

Scope your registry so a package name that does not exist in your scope cannot be pulled
from a public registry by accident:

```ini
# .npmrc
@acme:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NPM_TOKEN}
```

Scope registries are part of the manager's authorisation configuration and are read from
`.npmrc`; confirm the current syntax in the manager's docs.

## Lockfile conflicts

When two branches change the lockfile:

1. Do not hand-edit it to resolve the conflict.
2. Take one side, run the install, then run the install again if the manager reports the
   tree is out of date, and commit the result as its own change.
3. Explain in the pull request why the resolution looks the way it does, particularly if
   a version moved backwards or a new transitive dependency appeared.

A lockfile diff is the highest-signal part of a dependency upgrade. Read it. A 4000-line
lockfile diff with one unexpected new package is exactly the case you are looking for.

## Publishing a package

- Review what the published tarball will contain. Most managers publish only what the
  `files` field lists, but verify by packing locally and listing the contents.
- Include the licence file and a README.
- Verify the package name resolves to your registry and your account before publishing.
- Use a provenance or signature mechanism if your registry supports one, so consumers can
  verify the build origin.
- Publishing is not reversible in the way a git tag is. A yanked version can break every
  consumer that already resolved it.

## Incident response

If a dependency is compromised:

1. Pin to the last known good version in the lockfile and redeploy.
2. Audit what the malicious version's install script or runtime code reached: environment
   variables, credential files, CI secrets, network egress.
3. Rotate any credential that was present in the environment during the affected window.
   Treat it as compromised even if you see no evidence of access.
4. Add the advisory to the review checklist so the next person checks that package.
5. Post-mortem the detection gap. If the audit did not catch it, the audit is not your
   supply chain control.