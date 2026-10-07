#!/usr/bin/env bash
# Two-way sync of the local SQLite databases (data/metadata.db, data/feed.db)
# with the VM's /opt/quantitative-trading/data. See specs/008--db-sync/draft.md.
#
# Usage:
#   db_sync.sh [status|pull|push|sync|watch] [metadata|feed|all] [--force] [--overwrite]
#
# Verbs:
#   (no verb)  usage + status (safe default; touches nothing)
#   status     local vs remote sha256/mtime verdict per DB
#   pull       VM -> local (refuses to clobber local edits unless --overwrite)
#   push       local -> VM (market-hours guard; refuses to clobber VM edits
#              unless --force)
#   sync       two-way converge: pull remote-newer, push local-newer, stop on
#              divergence. Refused during KRX market hours unless --force.
#   watch      near-live mirroring: fswatch (if installed) + poll timer run
#              sync on every change either side. Warns at startup during KRX
#              market hours; the warning covers the whole session.
#
# Safety model (why not a FUSE mount):
#   - whole-file copy over IAP SSH (tar stream); SQLite is never mounted or
#     remotely opened, so FUSE/locking semantics never come into play.
#   - sha256 is captured before and after each transfer; if the VM wrote
#     mid-transfer the copy is retried instead of installed (torn-file guard).
#   - the file replaced by a pull/push is backed up first (keep 5 per DB).
#   - data/.db-sync-state.json records the last-synced sha256 per side, so
#     sync/pull/push refuse to silently discard the other side's work.
#
# Configuration via environment (set by the `just db` recipe):
#   QT_PROJECT, QT_ZONE, QT_VM, QT_APP_DIR, QT_DATA_DIR, QT_DB_POLL_SECONDS
set -euo pipefail

PROJECT="${QT_PROJECT:-quantitative-trading-510302}"
ZONE="${QT_ZONE:-us-central1-a}"
VM_NAME="${QT_VM:-quantitative-trading-vm}"
APP_DIR="${QT_APP_DIR:-/opt/quantitative-trading}"
DATA_DIR="${QT_DATA_DIR:-data}"
POLL_SECONDS="${QT_DB_POLL_SECONDS:-30}"
BACKUP_KEEP=5
DBS="metadata.db feed.db"
STATE_FILE="$DATA_DIR/.db-sync-state.json"

CMD=""
FILTER="all"
FORCE="false"
OVERWRITE="false"

usage() {
  cat <<EOF
Usage: just db [verb] [metadata|feed|all] [--force] [--overwrite]

Verbs:
  status   local vs remote state per DB (default when no verb is given)
  pull     VM -> local    (refuses to clobber local edits; --overwrite)
  push     local -> VM    (market-hours guarded; --force)
  sync     two-way converge (refused during KRX market hours; --force)
  watch    near-live mirroring (fswatch + ${POLL_SECONDS}s poll; warns when market is open)

DB filter: metadata | feed | all (default all)
EOF
}

die() { echo "error: $*" >&2; exit 1; }

# ── transport ────────────────────────────────────────────────────────────────

remote_exec() {
  gcloud compute ssh "$VM_NAME" --zone "$ZONE" --project "$PROJECT" \
    --tunnel-through-iap --quiet --command "$1"
}

# One SSH round-trip: "<db> <sha256> <mtime-epoch>" per DB, "<db> - -" if absent.
remote_info() {
  local cmd
  cmd=$(cat <<EOF
sudo sh -c 'for f in $DBS; do p="$APP_DIR/data/\$f"; if [ -f "\$p" ]; then echo "\$f \$(sha256sum "\$p" | cut -d" " -f1) \$(stat -c %Y "\$p")"; else echo "\$f - -"; fi; done'
EOF
)
  remote_exec "$cmd" 2>/dev/null || true
}

remote_sha() {
  remote_exec "sudo sha256sum '$APP_DIR/data/$1'" 2>/dev/null \
    | awk '{print $1}' || true
}

# ── local helpers ────────────────────────────────────────────────────────────

local_sha() {
  if [ -f "$DATA_DIR/$1" ]; then shasum -a 256 "$DATA_DIR/$1" | awk '{print $1}'; fi
}

local_mtime() {
  if [ -f "$DATA_DIR/$1" ]; then
    stat -f %m "$DATA_DIR/$1" 2>/dev/null || stat -c %Y "$DATA_DIR/$1" 2>/dev/null
  else
    echo "-"
  fi
}

fmt_time() {
  [ "$1" = "-" ] && { echo "-"; return; }
  date -r "$1" "+%Y-%m-%d %H:%M:%S" 2>/dev/null \
    || date -d "@$1" "+%Y-%m-%d %H:%M:%S" 2>/dev/null \
    || echo "$1"
}

prune_backups() { # $1 = backups dir, $2 = db filename
  ls -1t "$1" | grep "^$2\." | tail -n +$((BACKUP_KEEP + 1)) \
    | while read -r old; do rm -f "$1/$old"; done
}

backup_local() {
  local db=$1
  [ -f "$DATA_DIR/$db" ] || return 0
  mkdir -p "$DATA_DIR/backups"
  cp "$DATA_DIR/$db" "$DATA_DIR/backups/$db.$(date -u +%Y%m%dT%H%M%SZ)"
  prune_backups "$DATA_DIR/backups" "$db"
}

backup_remote() {
  local db=$1 ts cmd
  [ -n "$(remote_sha "$db")" ] || return 0
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  cmd=$(cat <<EOF
sudo mkdir -p '$APP_DIR/data/backups'
sudo cp '$APP_DIR/data/$db' '$APP_DIR/data/backups/$db.$ts'
cd '$APP_DIR/data/backups' && sudo sh -c "ls -1t | grep '^$db\.' | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -f"
EOF
)
  remote_exec "$cmd" >/dev/null 2>&1 || true
}

# KRX regular session: 09:00–15:30 KST, weekdays (same rule as `just update`).
market_open() {
  local day hm
  day=$(TZ=Asia/Seoul date +%u)
  hm=$(TZ=Asia/Seoul date +%H%M)
  [ "$day" -le 5 ] && [ "$hm" -ge 0900 ] && [ "$hm" -lt 1530 ]
}

# ── fingerprint state (best-effort optimistic concurrency) ──────────────────

record_state() { # $1 = db, $2 = local sha, $3 = remote sha
  mkdir -p "$DATA_DIR"
  python3 - "$STATE_FILE" "$1" "$2" "$3" <<'PY' || true
import json, os, sys, time
path, db, local_sha, remote_sha = sys.argv[1:5]
state = {}
try:
    with open(path) as f:
        state = json.load(f)
except Exception:
    state = {}
state.setdefault("dbs", {})[db] = {
    "local_sha": local_sha,
    "remote_sha": remote_sha,
    "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(state, f, indent=2)
os.replace(tmp, path)
PY
}

state_get() { # $1 = db, $2 = key ("local_sha" | "remote_sha")
  python3 - "$STATE_FILE" "$1" "$2" <<'PY' || true
import json, sys
try:
    print(json.load(open(sys.argv[1]))["dbs"][sys.argv[2]][sys.argv[3]])
except Exception:
    pass
PY
}

# ── verbs ────────────────────────────────────────────────────────────────────

selected_dbs() {
  if [ "$FILTER" = "all" ]; then echo "$DBS"; else echo "$FILTER"; fi
}

# Parse `remote_info` output for one DB -> "<sha> <mtime>" (or "- -").
remote_lookup() {
  local f sha mtime
  while read -r f sha mtime; do
    if [ "$f" = "$1" ]; then echo "$sha $mtime"; return; fi
  done <<< "$REMOTE_LINES"
  echo "- -"
}

verdict_for() { # $1 = db, $2 = local sha, $3 = remote sha
  local db=$1 lsha=$2 rsha=$3 bl br
  if [ "$lsha" = "-" ] && [ "$rsha" = "-" ]; then echo "absent-both"
  elif [ "$rsha" = "-" ]; then echo "local-only"
  elif [ "$lsha" = "-" ]; then echo "remote-only"
  elif [ "$lsha" = "$rsha" ]; then echo "in-sync"
  else
    bl=$(state_get "$db" local_sha)
    br=$(state_get "$db" remote_sha)
    if [ -z "$bl" ] || [ -z "$br" ]; then echo "diverged (no sync history)"
    elif [ "$lsha" = "$bl" ] && [ "$rsha" != "$br" ]; then echo "remote-newer"
    elif [ "$rsha" = "$br" ] && [ "$lsha" != "$bl" ]; then echo "local-newer"
    else echo "diverged"
    fi
  fi
}

pull_db() {
  local db=$1 attempt r_before r_after l_bl
  l_bl=$(state_get "$db" local_sha)
  if [ -f "$DATA_DIR/$db" ]; then
    if [ -n "$l_bl" ] && [ "$l_bl" != "$(local_sha "$db")" ] && [ "$OVERWRITE" != "true" ]; then
      echo "  $db: local has unpushed changes — refusing (use --overwrite; a backup is kept either way)" >&2
      return 1
    fi
    [ -n "$l_bl" ] || echo "  $db: no sync history — proceeding (local file backed up)" >&2
  fi
  for attempt in 1 2 3; do
    r_before=$(remote_sha "$db")
    if [ -z "$r_before" ]; then
      echo "  $db: not found on VM" >&2
      return 1
    fi
    backup_local "$db"
    if remote_exec "sudo tar -C '$APP_DIR/data' -cf - '$db'" 2>/dev/null \
        | tar -C "$DATA_DIR" -xf -; then
      r_after=$(remote_sha "$db")
      if [ "$r_before" = "$r_after" ]; then
        echo "  $db: pulled (sha ${r_after:0:12}…)"
        return 0
      fi
      echo "  $db: VM wrote during transfer — retrying (attempt $attempt/3)" >&2
    else
      echo "  $db: transfer failed — retrying (attempt $attempt/3)" >&2
    fi
  done
  echo "  $db: pull failed after 3 attempts" >&2
  return 1
}

push_db() {
  local db=$1 r_bl r_after lsha rc
  if market_open && [ "$FORCE" != "true" ]; then
    echo "  $db: KRX market open — push skipped (use --force to override)" >&2
    return 75
  fi
  r_bl=$(state_get "$db" remote_sha)
  if [ -n "$r_bl" ] && [ "$r_bl" != "$(remote_sha "$db")" ] && [ "$FORCE" != "true" ]; then
    echo "  $db: VM-side changed since last sync — refusing to clobber (use --force)" >&2
    return 1
  fi
  lsha=$(local_sha "$db")
  if [ -z "$lsha" ]; then
    echo "  $db: local file missing" >&2
    return 1
  fi
  backup_remote "$db"
  if tar -C "$DATA_DIR" -cf - "$db" \
      | remote_exec "sudo mkdir -p '$APP_DIR/data' && sudo tar -C '$APP_DIR/data' -xf -" 2>/dev/null; then
    r_after=$(remote_sha "$db")
    if [ "$r_after" = "$lsha" ]; then
      echo "  $db: pushed (sha ${lsha:0:12}…)"
      return 0
    fi
    echo "  $db: post-push sha mismatch — verify manually" >&2
    return 1
  fi
  echo "  $db: transfer failed" >&2
  return 1
}

cmd_status() {
  local db lsha lmt rsha rmt r verdict
  REMOTE_LINES=$(remote_info)
  if [ -z "$REMOTE_LINES" ]; then
    die "cannot reach VM (gcloud IAP ssh failed) — try 'just tunnel-ssh' or check the IAM context"
  fi
  for db in $(selected_dbs); do
    r=$(remote_lookup "$db")
    rsha=${r%% *}
    rmt=${r##* }
    lsha=$(local_sha "$db")
    lmt=$(local_mtime "$db")
    verdict=$(verdict_for "$db" "${lsha:--}" "$rsha")
    printf '%s\n' "$db"
    printf '  local : %s  (%s)\n' "${lsha:--}" "$(fmt_time "$lmt")"
    printf '  remote: %s  (%s)\n' "$rsha" "$(fmt_time "$rmt")"
    printf '  -> %s\n' "$verdict"
  done
}

# Converge one round. Prints actions; with QUIET=1 skips "in sync" chatter.
cmd_sync() {
  local db lsha rsha r verdict failures=0 rc
  REMOTE_LINES=$(remote_info)
  if [ -z "$REMOTE_LINES" ]; then
    die "cannot reach VM (gcloud IAP ssh failed)"
  fi
  for db in $(selected_dbs); do
    r=$(remote_lookup "$db")
    rsha=${r%% *}
    lsha=$(local_sha "$db")
    verdict=$(verdict_for "$db" "${lsha:--}" "$rsha")
    case "$verdict" in
      in-sync)
        [ "${QUIET:-0}" = "1" ] || echo "  $db: in sync"
        record_state "$db" "$lsha" "$rsha"
        ;;
      remote-newer|remote-only)
        echo "  $db: pulling ($verdict)"
        if pull_db "$db"; then
          record_state "$db" "$(local_sha "$db")" "$(remote_sha "$db")"
        else
          failures=$((failures + 1))
        fi
        ;;
      local-newer|local-only)
        echo "  $db: pushing ($verdict)"
        rc=0
        push_db "$db" || rc=$?
        if [ "$rc" -eq 0 ]; then
          record_state "$db" "$(local_sha "$db")" "$(remote_sha "$db")"
        elif [ "$rc" -ne 75 ]; then
          failures=$((failures + 1))
        fi
        ;;
      absent-both)
        echo "  $db: absent on both sides — nothing to do"
        ;;
      *)
        echo "  $db: DIVERGED — $verdict; nothing changed" >&2
        echo "        pick a winner explicitly:" >&2
        echo "          just db pull $db --overwrite   (VM wins; local backed up)" >&2
        echo "          just db push $db --force       (local wins; VM backed up)" >&2
        failures=$((failures + 1))
        ;;
    esac
  done
  [ "$failures" -eq 0 ]
}

cmd_sync_market_guard() {
  if market_open && [ "$FORCE" != "true" ]; then
    die "KRX market is open — 'just db sync' refused (the VM trader may be mid-write; use --force to override)"
  fi
}

cmd_pull() {
  local db fails=0 rc
  for db in $(selected_dbs); do
    rc=0
    pull_db "$db" || rc=$?
    if [ "$rc" -eq 0 ]; then
      record_state "$db" "$(local_sha "$db")" "$(remote_sha "$db")"
    else
      fails=$((fails + 1))
    fi
  done
  [ "$fails" -eq 0 ]
}

cmd_push() {
  local db fails=0 rc
  for db in $(selected_dbs); do
    rc=0
    push_db "$db" || rc=$?
    if [ "$rc" -eq 0 ]; then
      record_state "$db" "$(local_sha "$db")" "$(remote_sha "$db")"
    elif [ "$rc" -eq 75 ]; then
      echo "  $db: not synced (market guard)" >&2
      fails=$((fails + 1))
    else
      fails=$((fails + 1))
    fi
  done
  [ "$fails" -eq 0 ]
}

cmd_watch() {
  local have_fswatch=0
  command -v fswatch >/dev/null 2>&1 && have_fswatch=1
  if market_open && [ "$FORCE" != "true" ]; then
    echo "WARNING: KRX market is open — the VM trader may write at any moment." >&2
    echo "         This watch session may push/pull against it mid-cycle." >&2
    echo "         (Use --force to silence this warning.)" >&2
  fi
  echo "Watching $(selected_dbs | tr '\n' ' ')(poll ${POLL_SECONDS}s, fswatch: $([ "$have_fswatch" = 1 ] && echo on || echo off)) — Ctrl-C to stop"
  trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
  QUIET=1 cmd_sync || true   # converge immediately on start
  if [ "$have_fswatch" = 1 ]; then
    (
      fswatch --event Updated "$DATA_DIR/metadata.db" "$DATA_DIR/feed.db" 2>/dev/null \
        | while read -r _; do sleep 2; QUIET=1 cmd_sync || true; done
    ) &
  fi
  while true; do
    sleep "$POLL_SECONDS"
    QUIET=1 cmd_sync || true
  done
}

# ── main ─────────────────────────────────────────────────────────────────────

while [ $# -gt 0 ]; do
  case "$1" in
    status|pull|push|sync|watch) CMD="$1" ;;
    metadata) FILTER="metadata.db" ;;
    feed) FILTER="feed.db" ;;
    all) FILTER="all" ;;
    --force) FORCE="true" ;;
    --overwrite) OVERWRITE="true" ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
  shift
done
# Owner decision: no data-touching default — bare `just db` = usage + status.
[ -n "$CMD" ] || { usage; echo; CMD="status"; }

mkdir -p "$DATA_DIR"

case "$CMD" in
  status) cmd_status ;;
  pull) cmd_pull ;;
  push) cmd_push ;;
  sync) cmd_sync_market_guard; cmd_sync ;;
  watch) cmd_watch ;;
esac
