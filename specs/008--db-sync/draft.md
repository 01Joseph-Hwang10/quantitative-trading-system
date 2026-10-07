# Draft — `just db`: two-way sync of `metadata.db` / `feed.db` between local and VM

Desired UX: `just db` keeps the local `data/` SQLite files in sync with the
VM's `/opt/quantitative-trading/data/` — "like gcsfuse, so changes from the VM
reflect locally and vice versa." This draft defines what that actually means
for **SQLite files with a live remote writer**, and proposes a design that is
safe by default.

## Motivation

- The production VM is the source of truth during market days: the trader
  writes decisions/trades/account snapshots (15:00 KST) and the feed catch-up
  writes bars (09:00 KST) into `metadata.db` / `feed.db` on the VM.
- Locally, notebooks, tests, and mock-broker runs work against `./data/`, which
  silently drifts from production. There is currently no way to inspect
  production data locally other than ad-hoc `sqlite3` over SSH.
- Two-way is genuinely needed: production data comes *down* to local for
  analysis; local changes (seeded fixtures, manual corrections, restored
  snapshots) go *up* to the VM.

## The core constraint (why not literally gcsfuse)

SQLite requires coherent POSIX advisory locks, fsync ordering, and a stable
inode across the life of a connection. FUSE mounts (gcsfuse, sshfs, S3FS) do
not faithfully provide these:

- **gcsfuse**: object-store semantics; no correct file locking, cache coherence
  only within one client, and a second writer (the VM daemon) is invisible to
  the local kernel cache → guaranteed torn/lost updates.
- **sshfs**: locks are brokered over the SSH channel and are unreliable with
  concurrent writers; a local app holding the DB open while the remote daemon
  commits is the classic corruption scenario.
- Either way, the local `data/` directory would also need to stop being a real
  directory (cookie_secret, backups, etc. live there), and every local query
  pays network latency.

So the feature emulates the *behavior* (both sides converge) without emulating
a *filesystem*. Both DBs are small (a few KB–MB), so whole-file sync is cheap.

## Approach

Sync-level, not mount-level: a fingerprint-guarded whole-file copy over the
existing IAP SSH path, with three verbs plus an optional watch loop.

- Transport: `gcloud compute ssh --tunnel-through-iap --command "sudo tar …"`
  streaming (pull) / `tar | gcloud compute ssh --command "sudo tar -x"` (push).
  `sudo` is available passwordless via OS Login google-sudoers; files on the VM
  are owned by uid 1000 (container user) so a non-sudo push would fail — sudo
  also sidesteps that. No rsync needed on the VM; tar is always present.
  Local non-gcloud usage still works: `tar` over plain `ssh -p 2222 localhost`
  once `just tunnel-ssh` is up (same key as Ansible).
- Consistency: neither DB uses WAL (verified locally; same code runs on the
  VM), so the file is the unit of transaction. Copy is verified by comparing
  the remote sha256 before and after the transfer — if the VM wrote during the
  window, retry instead of storing a torn file.
- Optimistic-concurrency guard: `data/.db-sync-state.json` records the last
  synced sha256 of each DB per side. push/pull refuse (or prompt) when the
  target side changed since the last recorded sync — the "you will lose
  remote/local work" check.
- Backups before overwrite: the replaced file is archived to
  `data/backups/<db>.<utc-timestamp>.db` on the receiving side, keeping the
  last N (e.g. 5).
- Market-hours guard for push (and any destructive overwrite): reuse the
  `just update` KRX guard (09:00–15:30 KST weekdays, `--force` to bypass) —
  the VM daemon may be mid-cycle; never replace its DB while the market is
  open.
- Conflict policy: files are never auto-merged. When both sides diverged, the
  verbs stop and print the divergence (mtimes, sha256s, row counts via
  `sqlite3` over SSH where available); the human picks pull, push, or
  `--rename-mine` (keep both, older one becomes a timestamped backup).

## Commands (planned)

| Command | Behavior |
|---|---|
| `just db` | **Usage + status** (safe default): prints the verb list, then for each DB — local vs remote mtime, size, sha256, and a verdict: `in-sync` / `local-newer` / `remote-newer` / `diverged`. |
| `just db pull` | VM → local for both DBs (or one: `just db pull feed`). Refuses if local changed since last sync unless `--overwrite` (backs up local first). |
| `just db push` | Local → VM. KRX market-hours guarded; refuses if remote changed since last sync unless `--force` (backs up remote first, via sudo mv). |
| `just db sync` | The headline verb — two-way converge in the gcsfuse spirit: pull what only changed remotely, push what only changed locally, stop on both-changed divergence. **Refused outright during KRX market hours** (the VM daemon may be mid-cycle); `--force` overrides. |
| `just db watch` *(v1)* | Near-live mirroring: `fswatch` on local `data/*.db` (debounced, if installed) plus a poll timer (30s) → `sync` on every change either side. **Warns at startup when KRX market is open**; the warning is what satisfies the market-hours guard for the whole session (internal syncs are not re-blocked, otherwise a warned-and-running mirror could never push). `--force` silences the warning. |

All GCP-touching paths follow the house pattern: `eval "$(ctx use
quantitative-trading --export --confirm)"` first.

## Options considered

1. **gcsfuse / GCS bucket as the shared store** — rejected: object-store
   locking is incompatible with SQLite; would also force the VM containers
   onto network-backed DBs (latency on every 15:00 trade cycle) and adds a
   Terraform bucket + IAM change for no benefit at this file size.
2. **sshfs mount of `/opt/quantitative-trading/data`** — rejected: lock
   reliability with the live daemon writer, latency on every local query, and
   it replaces a real local directory. Documented here so the temptation is
   answered in writing.
3. **unison (bidirectional) over the IAP tunnel** — viable alternative for
   `just db sync`/`watch` (handles the state file and conflict detection
   itself), but adds a version-matched binary on both ends (macOS ↔ Debian)
   and another failure mode through IAP. Revisit if the watch loop proves
   annoying to maintain in bash.
4. **rsync via `ansible.posix.synchronize` playbook** — rejected for the
   interactive UX (playbooks are poor CLIs here) though a thin
   `ansible/db_sync.yml` remains a fallback if tar-streaming over
   `gcloud compute ssh` proves awkward.
5. **Chosen: tar-stream + fingerprints + guards** — zero new dependencies on
   the VM, consistent with existing justfile recipes, safe by default.

## Changes (planned)

- `justfile`: new `db` section — one thin `@db *args` recipe delegating to the
  script, so all five verbs share helpers.
- `scripts/db_sync.sh`: all sync logic (status/pull/push/sync/watch, market
  guard, fingerprint state, backups, sha verification).
- `data/.db-sync-state.json` + `data/backups/` (covered by the existing
  blanket `data/` gitignore — no ignore change needed).
- `README.md`: a short "Local DB sync" subsection under Development.
- No app (Python) changes; no Terraform/Ansible changes.

## Notes / known caveats

- A push replaces the live DB file the daemon has open. SQLite tolerates the
  inode swap only if the daemon isn't mid-transaction — hence: market-hours
  guard + sha re-check + remote backup. `--force` push during market hours is
  the owner's explicit risk. (If this ever matters for real, the clean fix is
  a tiny remote `docker compose restart trader` hook after push.)
- Divergence is expected to be rare in practice: the VM writes on schedule,
  local writes only when the user is developing. The state file is best-effort
  (`--overwrite` exists for when it's stale).
- `watch` does not detect VM-side writes between polls; the 30s default poll
  is plenty for a system that writes twice a day. Watching during market
  hours is the user's explicit choice — the startup warning exists so it is
  never accidental.
- First run has no state file: status must not lie, so with no recorded
  baseline it reports `unknown (no sync history)` instead of guessing.

## Verification

1. `just db status` shows `in-sync` right after a fresh `pull` from the VM,
   and sha256 matches `sqlite3 "PRAGMA integrity_check"` on both sides.
2. Local edit (mock-broker run) → `just db push` → remote sha256 matches;
   remote backup exists on the VM.
3. `just db push` and `just db sync` during simulated market hours refuse
   without `--force`; `just db watch` warns but starts.
4. Torn-write simulation: mutate the remote file between the pre- and post-
   sha check (manual `touch`+write) → command retries and does not install a
   corrupt copy.
5. Divergence: edit both sides → `just db sync` stops with a clear report and
   changes nothing.
6. Bare `just db` (no verb) prints usage + the status table and touches
   nothing.

## Market-hours policy (owner decisions)

| Command | During KRX 09:00–15:30 KST weekdays |
|---|---|
| `just db sync` | **Refused** — requires `--force` (the trader may be mid-write; a push would swap its DB out from under it). |
| `just db push` | Refused — requires `--force` (unchanged). |
| `just db pull` | Allowed — local-only overwrite, VM untouched. |
| `just db watch` | **Warns loudly at startup**, keeps running (the user opted into mirroring; the warning documents the race). `--force` silences the warning. |

## Resolved decisions (owner)

1. **Verb style**: explicit subcommands — the converge verb is `just db
   sync`; bare `just db` shows usage + status (no data-touching default).
2. **Backup retention**: N=5 per DB per side.
3. **Scope**: all five verbs ship in v1, including `watch`.
4. **Market hours**: sync refused / push refused / pull allowed / watch warns
   (table above); `--force` overrides sync, push, and the watch warning.
