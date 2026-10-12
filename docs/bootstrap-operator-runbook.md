# First-User Bootstrap Operator Runbook

## Purpose and scope

The `evidenceforge-bootstrap` CLI provisions the initial EvidenceForge user,
an initial workspace, an OWNER membership, an opaque bearer token, and an audit
event. It is an operator-only, one-time operation. It is not a public
registration endpoint or a general user-creation command.

The operation is guarded by a PostgreSQL transaction-level advisory lock,
migration-head validation, a transaction-isolation check, empty security-boundary
table checks, and a prior-bootstrap audit-marker check. The user, workspace,
membership, token record, and completion event are created atomically.

**Do not run this command against an existing or shared database until its
identity and eligibility have been deliberately verified.** Never use it as a
way to reset, repair, or recreate an existing deployment.

## Preconditions

Before proceeding, confirm all of the following:

- You are the authorized operator for the intended environment and have
  approval to create its first user.
- The target database is the intended PostgreSQL database, not a test, staging,
  or production database chosen by mistake.
- Any required backup has completed and its recovery procedure is known.
- The initial identity subject is the exact subject from the approved identity
  provider or identity source. Do not invent a subject to work around identity
  integration.
- The application image contains the bootstrap CLI.
- You understand how the issued bearer token will be captured and stored under
  your organization's approved secret-handling procedure.

The CLI refuses provisioning when users, API tokens, workspaces, or workspace
memberships already exist. A prior `auth.bootstrap_completed` audit marker also
blocks repeat provisioning. Existing audit events alone do not necessarily
prevent bootstrap. Do not assume that a database is eligible just because the
application tables exist.

## 1. Confirm the target environment

From the approved deployment directory, inspect the Compose service status:

```powershell
docker compose ps
```

Confirm the configured database name without printing the full connection URL
or its credentials:

```powershell
docker compose exec -T api python -c "import os; from sqlalchemy.engine import make_url; print('Target database:', make_url(os.environ['DATABASE_URL']).database)"
```

Verify that the printed database is the intended target. Do not paste the
connection URL, password, token, or other secret-bearing configuration into a
terminal transcript, issue, chat, or commit.

For a local Compose deployment, the API obtains `DATABASE_URL` from its
container environment, typically using the Compose service hostname `db`.
Follow the environment-specific deployment procedure if you are not using the
repository's Compose stack.

## 2. Check the runtime and migration revision

Check that the CLI is installed:

```powershell
docker compose exec api evidenceforge-bootstrap --help
```

This displays usage information only; it does not provision an identity.

Check the migration revision reported by the running API container:

```powershell
docker compose exec api alembic current
docker compose exec api alembic heads
```

Confirm that the current database revision matches the repository head.
Bootstrap performs its own migration-head check and refuses to continue if the
revisions do not match.

For the local Compose stack, readiness can also be checked through the
frontend's configured API proxy:

```powershell
Invoke-WebRequest `
  -Uri 'http://localhost:3000/backend-api/health/ready' `
  -TimeoutSec 15 `
  -UseBasicParsing
```

Readiness confirms database connectivity; it does **not** prove that bootstrap
is eligible or that the first-user identity has been provisioned.

## 3. Perform the one-time bootstrap

Use the approved deployment operator session. Replace every placeholder with
the authorized initial identity and workspace values before running the command.
Do not run it merely to test whether the CLI works.

```powershell
docker compose exec api evidenceforge-bootstrap `
  --external-subject "<EXACT_IDENTITY_PROVIDER_SUBJECT>" `
  --email "<INITIAL_OWNER_EMAIL>" `
  --display-name "<INITIAL_OWNER_DISPLAY_NAME>" `
  --workspace-name "<INITIAL_WORKSPACE_NAME>" `
  --token-ttl-days 30
```

`--token-ttl-days` accepts a value from 1 through 365; the default is 30 days.
The external subject must not have leading or trailing whitespace.

The CLI returns a refusal exit code when a safety precondition is not met.
An unexpected database or runtime failure returns a failure exit code. Do not
bypass a refusal by editing the database or disabling a guard.

## 4. Handle the bearer token securely

After the transaction commits successfully, the CLI displays the newly issued
bearer token once. The token is not stored as plaintext in the database; the
database stores its hash. The CLI also reports the expiration time in UTC.

- Capture the token only through an approved, access-controlled operator
  procedure and store it in the approved secret manager or secure credential
  store.
- Treat the displayed token as a secret equivalent to a password. Anyone who
  obtains a usable token may be able to act as its owner until it expires or is
  revoked.
- Do not put the token in source code, `.env` files, command arguments, shell
  history, screenshots, logs, tickets, chats, or commits.
- Be aware that terminal transcripts and console capture may retain command
  output. Follow the deployment's approved procedure for handling that output.
- Do not assume the token can be retrieved later. Plan secure capture before
  starting the command.

If the token is exposed or lost, follow the deployment's credential-incident
and recovery procedure. This bootstrap operation is deliberately not repeatable;
do not rerun it to try to obtain another token.

## 5. Verify after provisioning

Using an approved API client with the token supplied through its secure
credential mechanism, make an authenticated request to `GET /workspaces`.
Do not place the token directly in a command or print the Authorization header.

Confirm through approved administrative checks that:

- Exactly the intended initial user and workspace were created.
- The user has an OWNER membership in that workspace.
- A token record exists with a hash and the expected expiry.
- The `auth.bootstrap_completed` audit event exists.
- The bearer token authenticates successfully and its expiry is documented
  according to operational policy.

Health checks alone are insufficient to prove these conditions.

## Failure handling

If bootstrap is refused, read the reason and verify the environment, migration
head, transaction isolation, existing security-boundary records, and prior
bootstrap marker. Correct the underlying deployment or eligibility issue only
through the approved procedure.

If an unexpected failure occurs after the operation begins, the CLI deliberately
avoids displaying sensitive exception details. Verify the database state and
audit marker before taking another action. Do not blindly retry when the
transaction outcome is uncertain.

Never delete the Compose database volume, reset the database, remove audit
events, or edit production records directly to make bootstrap succeed.

## After the first user

This CLI is for initial provisioning only. The API does not expose public user
registration or token issuance. Do not reuse this command to onboard additional
users. Use an approved identity and user-lifecycle procedure for subsequent
access; if your deployment has no such procedure, define it before granting
additional access.

## Operational record

Record non-secret operational metadata according to policy: environment,
database name, operator/change reference, completion time, token expiry, and
verification outcome. Never record the raw bearer token or the database
connection string in the runbook or an audit note.
