# Multica Mobile (iOS)

Expo + React Native iOS client for Multica. Independent from web/desktop — shares types and pure utilities from `@multica/core/`. See [`AGENTS.md`](./AGENTS.md) for mobile architecture and development rules; `package.json` records the current dependency versions.

## Managed iPhone development

This platform-managed checkout builds native iOS artifacts on the registered M2
build host. M4 dispatches jobs, checks returned artifacts and operates the leased
phone. Use a task worktree and the shared client workflow below. Existing
production installations are preserved; the development app uses its own bundle.

A production remote recipe has not been declared here. Legacy device/production
commands stop instead of compiling on M4 or silently selecting the staging app.
Signed development app, branch IPA and shared XCTest driver recipes are available.

## Scripts

| Command | What it does | Backend |
|---|---|---|
| `pnpm dev:mobile` | Metro only (reuse existing install) | local (`.env.development.local`) |
| `pnpm dev:mobile:staging` | Metro only (reuse existing install) | staging (`.env.staging`) |
| `pnpm dev:mobile:prod` | Metro only (reuse existing install) | production (`.env.production`, overridden by `.env.production.local`) |
| `pnpm ios:mobile` / `pnpm ios:mobile:staging` | Dispatch M2 Debug test-app build; no install | verified worktree public API |
| `apps/mobile/scripts/ios-run.sh --build-device` | Same managed build-only entry from repo root | verified worktree public API |
| `scripts/dev/publish_ios_test_build.sh` | Dispatch M2 standalone branch IPA; verify and publish it | verified worktree public API |
| Shared `ios_debug.py smoke --target simulator --app <existing-app>` | Explicit diagnostic using a compatible built app | verified worktree public API |

Managed native jobs use committed source and shared resource admission. Missing
remote configuration, account permission or signing assets stops the job. No
native compiler fallback runs on M4. Manual Metro-only commands above do not
replace the managed smoke wrapper's ownership and runtime checks.

### Regenerating iOS after the scene lifecycle migration

The app uses Expo SDK 57's scene lifecycle support for builds linked against
the iOS 27 SDK. `app.config.ts` enables `ios.enableSceneSupport` through
`expo-build-properties`; Expo owns window creation and forwards scene lifecycle
and linking events. This requires Expo 57.0.23+ and expo-build-properties
57.0.20+; adding a scene manifest alone to an SDK 55 app is insufficient.
See [Expo's migration guidance](https://github.com/expo/fyi/blob/main/ios-scene-lifecycle.md).

SDK 57's scene backport does not include the later scene-aware presenter lookup
used by native modules such as `expo-image-picker`. The workspace applies
`apps/mobile/patches/expo-modules-core@57.0.21.patch`, a direct backport of Expo's
[#46956](https://github.com/expo/expo/pull/46956) and
[#48319](https://github.com/expo/expo/pull/48319), so avatar, chat, and issue
image pickers and document attachments resolve their presenting window from the
foregrounded scene. pnpm requires patched dependencies to be registered at the
workspace root, but the patch source stays with the mobile app so patch-only
changes remain in the mobile CI scope. Keep the patch until Expo backports the
change to SDK 57 or mobile moves to SDK 58+.

`eslint-config-expo` and TypeScript are temporarily excluded from
`expo install --check`: upgrading them enables additional compiler lint rules
that need a focused cleanup. [#9165](https://github.com/multica-ai/multica/issues/9165)
tracks removing the exclusions and aligning both tools with SDK 57.

After updating dependencies, regenerate an existing native project once (save
any local native customizations first; `ios/` is generated and ignored):

```bash
cd apps/mobile
APP_ENV=production pnpm exec expo prebuild --clean --platform ios
```

Use `development` or `staging` instead for those variants, then run the usual
build script. Keep normal development builds on the incremental prebuild path.
For native validation, test a Release cold launch to sign-in, cold and warm
`multica://` links, background/foreground transitions, and the Debug dev-client
in each variant. Also open the image picker from profile, chat, and the issue
editor, select an image, and confirm the upload completes. From the issue editor,
also attach a document and confirm its picker dismisses and the upload completes.
Unit tests do not replace this device validation.

## Shared client verification

The project `dev-platform.json` declares the `client` and `ios_debug` adapters for
`~/dev-platform`. Managed acceptance defaults to a registered physical iPhone from
the shared `default` pool; no phone identifier is stored in this repository.
The phone uses the separate `cn.comosha.multica.staging` test app, preserving the
production app and existing data. The JSON smoke flow launches the app and opens
issue creation without saving an issue. `--target simulator` explicitly selects
the retained Maestro flow; it cannot satisfy default physical-device acceptance.
Metro uses the worktree port plus 1000. `DPNativeInputsSHA256` records the app config, dependency manifests,
lockfile, native patch, and icon. Native changes require a matching development
client; ordinary JavaScript edits remain incremental.

`app.config.ts` places `apiUrl` and `buildCommit` in Expo `extra.clientRuntime`.
API requests, WebSockets, and attachment links consume that one configuration
through `expo-constants`. The artifact inspector reads the same
`EXConstants.bundle/app.config` from the actual IPA, not build logs or arbitrary
strings in the binary. Mac builds load their branch endpoint from the same
`client-runtime.json` that the inspector reads inside `app.asar`.

The runtime probe requests the actual local Metro iOS manifest with the same
headers as Expo's development launcher. It reads
`extra.expoClient.extra.clientRuntime`, which supplies `Constants.expoConfig` in
development, and checks the installed bundle ID, project directory, Metro listener,
JavaScript source, and assigned public backend. On a phone, it also binds the
installed app to the shared install receipt (device, bundle, artifact hash and
source revision). It never substitutes the generated local
config or an environment variable for this observation. The response hash is
recorded for evidence; Expo generates a new manifest ID and timestamp per request,
so runtime consistency uses the endpoint and source revision, not that hash.

The managed Metro wrapper starts only after the copy database and backend are
verified. It exposes the worktree web proxy and Metro through the existing
Tailscale install hub, checks both routes, then atomically writes `.env.ios-device`.
`EXPO_PACKAGER_PROXY_URL` controls the URLs in Expo's actual manifest; the phone's
API URL is the verified public testbench URL, never the phone's localhost. Local
listener ownership is still checked against this worktree. Before and after the
page flow, `hub expose --check` rechecks the existing Metro route without creating
or changing it. The wrapper supplies
only a credential minted in the isolated copy and disables automatic dotenv
loading to prevent production fallback. It stops only its own Metro process group.

The build-only entry is `apps/mobile/scripts/ios-run.sh --build-device` from the
repository root. M4 verifies the worktree backend and copied database before
writing the mode-0600 `data/ios-remote-input.json`. The shared allowlist permits only
this generated file; it contains the public API, source revision, recipe and build
number, plus a copy-only credential for a standalone branch IPA. Debug builds
leave that credential empty and receive it later from the managed local Metro.
No database, worktree identity, private key or complete env file is transferred.

M2 installs locked mobile dependencies, regenerates the staging native project,
compiles with a generic iOS destination and uses its own team signing identity.
The input helper validates the shallow checkout and source revision, suppresses
automatic dotenv loading and redacts the copy credential from build output. Its
children stay inside the worker's process group for cancellation. M4 checks the
returned source, signature and artifact, then verifies that the backend instance
and database identity have not changed. Verified jobs acknowledge remote cleanup
of the staged private input; local receipt and job records remain for review.

The development app lands at
`build/ios-device/DerivedData/Build/Products/Debug-iphoneos/MulticaStaging.app`.
`DPBuildCommit` and `DPNativeInputsSHA256` come from the actual generated Info.plist.
The shared agent-device XCTest driver is built independently by dev-platform on M2 and verified for the registered phone and signing team. Business projects reuse its tool manifest; they do not declare their own UI driver recipe.

After an approved artifact is available, use the shared
`tools/sim/ios_debug.py smoke --issue MU-n --dir <worktree>` entry to claim a registered device, install
without deleting data and validate the actual page. The updated physical, routing
and resource APIs are supplied by dev-platform PR 118; set `DEV_PLATFORM_ROOT`
to its reviewed worktree until merged. Simulator use remains an explicit override
and requires its matching installed staging development client.

New test databases are cloned from a hashed source snapshot. A non-product
`dev_platform` schema stores the source ID, snapshot hash, and unique clone ID.
Verification checks the running backend's effective connection and queries the
host-published database port in a read-only transaction. Existing databases without
this marker remain unverified. Never label an old copy with today's snapshot hash.

For an unverified or incomplete copy, run `scripts/dev/dp_env.sh renew-copy --no-start`.
It verifies service/container ownership, backs up local environment and registry
files, stops only this worktree's services, and creates a new database from a fresh
read-only snapshot. The old database remains under its original name. Recovery
records live in `data/client-recovery/`; omit `--no-start` to restart services after
cloning. A pending marker from a failed restore blocks subsequent startup.

`python3 scripts/dev/client_adapter.py repair-registry --dry-run` reports conflicts.
`--apply` only quarantines registrations for the same physical worktree and same
database when their old ports and recorded processes are inactive. It never drops
a database. Other database identities or live processes require ownership review.

Before publication, the shared contract verifies local services and data. After
publication, it reads the artifact hash, revision, and endpoint, then checks that
the backend, frontend proxy, and public endpoint identify the same running instance.
The first publication can establish the public route; its absence never counts
as a successful post-publication check.

`MULTICA_CLIENT_TOOL` and `MULTICA_IOS_HUB`/`MULTICA_MAC_HUB` can point to a reviewed
dev-platform worktree before that change is merged. Missing contract support or
`hub.py artifact-path` blocks publication. Targeted checks include
`python3 scripts/dev/client_adapter_test.py`, `python3 scripts/dev/ios_device_test.py`,
`python3 scripts/dev/ios_remote_test.py`,
`bash apps/mobile/scripts/ios-run.test.sh`, related mobile Vitest tests, and the
desktop client-runtime hook tests. These do not replace simulator/device acceptance.

## First-time setup

### Trusted local installations

For a private iOS or Mac installation that should open without a sign-in form,
create a personal access token for the intended account and put it in an
untracked local env file for each client:

```text
apps/mobile/.env.local:  EXPO_PUBLIC_LOCAL_AUTH_TOKEN=<personal access token>
apps/desktop/.env.local: VITE_LOCAL_AUTH_TOKEN=<personal access token>
```

Use a separate token per client so either installation can be revoked alone.
These values are included in the built app, so distribute such builds only to
trusted devices. Without the local variable, the normal sign-in flow remains.
The worktree setup links these private files from the main checkout. Branch
packages use the account represented by these credentials to mint a separate
credential against the worktree's cloned database. The packaging script verifies
the worktree identity, serving process, current revision, and database provenance
before creating a credential. Conflicting old environment registrations block
packaging rather than selecting the first matching record. Packaging also stops if
that account or its workspaces are missing from the snapshot. The branch credential
and endpoint are included in the package; install it only on trusted devices.

`.env.staging` is committed (public staging URL). `.env.development.local` is gitignored — copy the template once:

```bash
cp apps/mobile/.env.example apps/mobile/.env.development.local
# then edit EXPO_PUBLIC_API_URL inside it to your Mac's LAN IP, e.g. http://192.168.1.42:8080
```

If your Apple ID isn't on the Multica Apple Developer team yet, also set `EXPO_BUNDLE_IDENTIFIER_DEV` to a reverse-domain you own (e.g. `com.yourname.multica.dev`). For a personal production build, set `EXPO_BUNDLE_IDENTIFIER_PROD` in `.env.production.local`.

For staging builds, export `EXPO_BUNDLE_IDENTIFIER_STAGING=com.yourname.multica.staging` before running an `ios:*:staging` script. Each override applies only to its named variant, so development, staging, and production can stay installed side by side.

If your Apple ID belongs to more than one Apple Developer team, also set `EXPO_APPLE_TEAM_ID` to the team that should sign your builds. Unlike the bundle id overrides it applies to every variant, and it is re-applied on each run — so it also fixes a checkout that has already latched onto the wrong team.

## Run on the registered iPhone

Remote native recipes first require Xcode 26.4 or newer and Node 22.13.0 or newer on M2.
The selected build host may set `DEVELOPER_DIR`; the preflight verifies that actual
Xcode before installing dependencies or querying signing. An older host blocks the
job and never falls back to compiling on M4.

From a clean, committed task worktree, use the build-only entry above, then run:

```bash
DEV_PLATFORM_ROOT=~/dev-platform ~/dev-platform/tools/sim/ios_debug.py smoke --issue MU-n --dir "$PWD"
```

The shared workflow starts the isolated local services, claims a registered phone,
installs the separate test app without deleting its data and asserts the actual
issue-creation page. The Debug app needs the managed Metro service. A standalone
branch IPA is built through `scripts/dev/publish_ios_test_build.sh`; its API and
copy credential are embedded, and its expiry follows its actual provisioning file.

## Explicit simulator diagnostics

Shared smoke accepts `--target simulator --app <existing-compatible-app>` and the
YAML `simulator_flow`. Its evidence cannot satisfy physical-device acceptance.
There is no reviewed remote simulator build recipe yet; raw Expo simulator builds
stop before compilation. No native compiler runs on M4, including diagnostic paths.

Managed endpoint changes go through the worktree identity and client contract.
Do not hand-edit a production URL or the phone's localhost into the test build.


## Formal remote release

The fixed install-hub app uses the source-declared `production-ipa` recipe. Prebuild,
archive and export all run with `APP_ENV=production` and `EXPO_NO_DOTENV=1`. The
formal bundle is `cn.comosha.multica`; its existing Ad Hoc signing requirement is
preserved. M2 must have a valid distribution identity for the configured team;
export uses only `release-testing` and never falls back to development signing.

The private install-hub settings remain the authority for formal API/Web URLs.
The reviewed input schema is `data/ios-production-input.json` inside the remote
checkout: version, recipe, source_commit, build_number, api_url, web_url, auth_token,
team, bundle_id and required_signing. Only the prepared single JSON may be
allowlisted; no full env file or test database is transferred. Package inspection
checks the actual embedded production environment, API, bundle and commit.

Credential preparation reads only the existing main checkout's
`apps/mobile/.env.local` `EXPO_PUBLIC_LOCAL_AUTH_TOKEN` after checking a clean,
matching main source. The generated single JSON is stored with mode 0600 at
`INSTALL_HUB_HOME/inputs/multica-ios-production-input.json` (default private
install-hub state); configure that exact source path in the remote input allowlist.
No token from a copied database or a newly selected local account may substitute
for it. The remote worker filters input secrets from logs and removes its task's
private inputs after successful receipt/package verification. This code path has
only fake-data validation so far; formal build/publish has not been run.
