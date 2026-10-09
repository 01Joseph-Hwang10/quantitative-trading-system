# Done — `just db`: two-way sync of `metadata.db` / `feed.db` between local and VM

Implementation record for `plan.md` (which holds the motivation, rejected
options, and safety model — not repeated here). Everything below is what
actually shipped.

## Surface

| File | Change |
|---|---|
| `scripts/db_sync.sh` | All sync logic (429 lines): verbs, transport, guards, fingerprints, backups. |
| `justfile` | One thin `@db *args` recipe: loads the GCP context via `ctx use quantitative-trading --export --confirm`, exports `QT_PROJECT` / `QT_ZONE` / `QT_VM` / `QT_APP_DIR`, and delegates to the script. |
| `README.md` | "Local DB sync (`just db`)" subsection under Development: verbs, safety rails, pointer to this spec. |
| `data/.db-sync-state.json`, `data/backups/` | Created at runtime; covered by the blanket `data/` gitignore (confirmed). |

No Python app changes, no Terraform/Ansible changes — as planned.

## Commands (shipped)

```bash
just db                     # usage + status — safe default, touches nothing
just db sync                # two-way converge; refused during KRX market hours
just db pull [db]           # VM -> local; refuses to clobber local edits (--overwrite)
just db push [db]           # local -> VM; market-hours guarded (--force)
just db watch [db]          # fswatch (if installed, Updated events + 2s debounce)
                            #   + 30s poll -> sync; warns (not refuses) when market open
just db status [db]         # status only
```

`[db]` = `metadata` | `feed` | `all` (default). Flags: `--force` (overrides
market guard and fingerprint refusals), `--overwrite` (pull-side clobber
consent). Extra tuning knobs via env: `QT_DATA_DIR`, `QT_DB_POLL_SECONDS`.

## Status verdicts (as implemented)

`in-sync` · `local-newer` · `remote-newer` · `diverged` · plus edge verdicts
the draft didn't enumerate: `local-only` / `remote-only` (file exists on one
side only — treated as pull/push candidates by `sync`, not conflicts) and
`absent-both`. With no fingerprint history, status reports
`diverged (no sync history)` instead of guessing — the "first run must not
lie" rule from the draft.

## Safety rails (all shipped)

- **Torn-file guard**: sha256 of the remote file captured before and after
  each pull; on mismatch the transfer retries (up to 3 attempts) instead of
  installing a file the VM daemon half-wrote. Push verifies the remote sha
  equals the local sha after upload.
- **Fingerprint state** (`data/.db-sync-state.json`, updated via a small
  embedded `python3` snippet with atomic `os.replace`): records
  `local_sha` / `remote_sha` / `synced_at` per DB after every successful
  sync. `pull` refuses when local changed since last sync (`--overwrite`);
  `push` refuses when the VM changed since last sync (`--force`).
- **Backups**: the file replaced by a pull/push is copied to
  `data/backups/<db>.<UTC timestamp>.db` (local) or
  `<app>/data/backups/...` (VM, via `sudo`), keep-last-5 per DB per side.
- **Market-hours guard** (KRX 09:00–15:30 weekdays, same rule as
  `just update`): `sync` and `push` refused without `--force`; `pull`
  always allowed; `watch` prints a loud startup warning instead of refusing
  (the warning covers the whole session; `--force` silences it). A market-
  guarded `push` exits 75 so `sync` doesn't count it as a hard failure.
- **Divergence**: never auto-merged. `sync` stops, reports the verdict, and
  tells the user to pick a winner (`pull <db> --overwrite` or
  `push <db> --force`).
- **Transport**: whole-file tar over `gcloud compute ssh --tunnel-through-iap`
  with `sudo` on the VM side (uid-1000-owned files + passwordless
  google-sudoers). One `remote_info` SSH round-trip gathers
  `<db> <sha> <mtime>` for both DBs for status/sync.

## Deviations from the draft

1. **`--rename-mine` not implemented** — divergence resolution ended up as
   the two explicit "pick a winner" commands above, which are simpler and
   self-documenting; the keep-both rename felt redundant given every
   overwrite path already makes a timestamped backup.
2. **Plain-SSH fallback not implemented** — the draft mentioned tar over
   `ssh -p 2222 localhost` for non-gcloud use. Dropped: every other DB/
   deploy command in this repo goes through `gcloud compute ssh` IAP, and a
   second transport path would need its own tested key/`sudo` story.
3. **Push retry loop not implemented** — the draft's torn-write retry was
   specified for transfers generally; it landed as a 3-attempt retry on
   pull and a verify-after-push on push (a push failure surfaces a sha
   mismatch message rather than looping).
4. **`watch` also converges immediately on start** (one quiet `sync` round)
   before entering the fswatch/poll loop.

## Verification status

Syntax-checked (`bash -n`). The live checklist in `plan.md` (status
truthfulness, backup creation, market-hours refusals, torn-write retry,
divergence stop, bare `just db` no-op) requires the VM and is exercised via
the commands above on first real use.
