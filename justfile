# Quantitative trading system — build, provision, deploy, day-2 operations.
#
# All GCP-touching recipes activate the `quantitative-trading` IAM context
# first. Requires: docker, gcloud, terraform, ansible-playbook, just.

project := "quantitative-trading-510302"
region := "us-central1"
zone := "us-central1-a"
registry_host := "us-central1-docker.pkg.dev"
ar_url := registry_host + "/" + project + "/quantitative-trading"
image := ar_url + "/app"
tf_dir := "terraform"
ansible_dir := "ansible"
vm_name := "quantitative-trading-vm"
app_dir := "/opt/quantitative-trading"
tunnel_port := "2222"

# Default recipe: list available commands.
default:
    @just --list

# ── Build & push ─────────────────────────────────────────────────────────────

# Print the image reference for the current git state (sha, -dirty suffix).
@tag:
    #!/usr/bin/env bash
    set -euo pipefail
    sha=$(git rev-parse --short HEAD)
    if [ -n "$(git status --porcelain)" ]; then sha="${sha}-dirty"; fi
    echo "{{ image }}:${sha}"

# Build the image and tag it :latest plus :<git-sha>.
@build:
    #!/usr/bin/env bash
    set -euo pipefail
    tag=$(just tag)
    docker build -t qt-app:latest -t "{{ image }}:latest" -t "$tag" .
    echo "Built $tag"

# Build for linux/amd64 (the VM architecture) and push both tags.
# The VM is amd64 — never push the local arm64 build for deployment.
@push:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    token=$(gcloud auth print-access-token)
    echo "$token" | docker login -u oauth2accesstoken --password-stdin https://{{ registry_host }} >/dev/null
    tag=$(just tag)
    docker buildx build --platform linux/amd64 \
      -t "{{ image }}:latest" -t "$tag" --push .
    echo "Pushed {{ image }}:latest and $tag (linux/amd64)"

# ── Provision (Terraform) ────────────────────────────────────────────────────

# terraform init + apply; then (re)generate the Ansible inventory.
# The first `gcloud compute ssh` call registers this machine's key with OS
# Login, and the OS-Login username is resolved from the profile.
@provision:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    terraform -chdir={{ tf_dir }} init -input=false
    terraform -chdir={{ tf_dir }} apply -auto-approve
    # Register OS Login keys (no-op if already registered) and warm up IAP.
    gcloud compute ssh {{ vm_name }} --zone {{ zone }} --project {{ project }} \
      --tunnel-through-iap --quiet --command "true"
    oslogin_user=$(gcloud compute os-login describe-profile \
      --format="value(posixAccounts[0].username)")
    cat > {{ ansible_dir }}/inventory.ini <<EOF
    [vm]
    quantitative-trading-vm ansible_host=127.0.0.1 ansible_port={{ tunnel_port }} ansible_user=${oslogin_user}

    [vm:vars]
    ansible_ssh_private_key_file=~/.ssh/google_compute_engine
    EOF
    echo "Inventory written (user=${oslogin_user}, port={{ tunnel_port }})"

# Show terraform outputs.
@outputs:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    terraform -chdir={{ tf_dir }} output

# ── Connectivity ─────────────────────────────────────────────────────────────

# Ensure a background IAP SSH tunnel exists on 127.0.0.1:2222 for Ansible.
@tunnel-ssh:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    if nc -z 127.0.0.1 {{ tunnel_port }} 2>/dev/null; then
        exit 0  # already up
    fi
    echo "Starting background IAP tunnel on 127.0.0.1:{{ tunnel_port }}…"
    nohup gcloud compute start-iap-tunnel {{ vm_name }} 22 \
      --zone {{ zone }} --project {{ project }} \
      --local-host-port=127.0.0.1:{{ tunnel_port }} --quiet \
      > /tmp/qt-iap-tunnel.log 2>&1 &
    for _ in $(seq 1 30); do
        nc -z 127.0.0.1 {{ tunnel_port }} 2>/dev/null && exit 0
        sleep 1
    done
    echo "IAP tunnel failed to start — see /tmp/qt-iap-tunnel.log" >&2
    exit 1

# Port-forward the monitor to localhost:8501 (for Google OIDC login).
@tunnel:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    echo "Connecting — open http://localhost:8501 in your browser (Ctrl-C to stop)…"
    gcloud compute ssh {{ vm_name }} --zone {{ zone }} --project {{ project }} \
      --tunnel-through-iap --quiet -- -L 8501:localhost:8501 -N

# ── Deploy & day-2 operations ────────────────────────────────────────────────

# Full first-time deploy: bootstrap the VM, then deploy the current build.
# Pass `true` to build+push first: `just deploy true`.
@deploy build_first="false":
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    if [ "{{ build_first }}" = "true" ]; then
        just build
        just push
    fi
    tag=$(just tag)
    just tunnel-ssh
    ANSIBLE_CONFIG={{ ansible_dir }}/ansible.cfg ansible-playbook {{ ansible_dir }}/bootstrap.yml
    ANSIBLE_CONFIG={{ ansible_dir }}/ansible.cfg ansible-playbook {{ ansible_dir }}/deploy.yml -e tag="${tag##*:}"

# Rolling update: market-hours guard → build → push → guarded remote restart.
# Bypass the guard with `just update --force`.
@update *args="":
    #!/usr/bin/env bash
    set -euo pipefail
    force="false"
    for arg in {{ args }}; do
        if [ "$arg" = "--force" ]; then force="true"; fi
    done
    if [ "$force" != "true" ]; then
        # KRX regular session: 09:00–15:30 KST, weekdays. Holidays are not
        # detected locally — the trader daemon's own market-calendar check
        # covers those; this guard protects only the deploy window.
        day=$(TZ=Asia/Seoul date +%u)
        hm=$(TZ=Asia/Seoul date +%H%M)
        if [ "$day" -le 5 ] && [ "$hm" -ge 0900 ] && [ "$hm" -lt 1530 ]; then
            echo "KRX market is open — update rejected (use 'just update --force' to override)" >&2
            exit 1
        fi
    fi
    just build
    just push
    tag=$(just tag)
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    just tunnel-ssh
    ANSIBLE_CONFIG={{ ansible_dir }}/ansible.cfg ansible-playbook {{ ansible_dir }}/update.yml -e tag="${tag##*:}"

# Manual rollback to the previous tag (never runs automatically).
@rollback:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    just tunnel-ssh
    ANSIBLE_CONFIG={{ ansible_dir }}/ansible.cfg ansible-playbook {{ ansible_dir }}/rollback.yml

# Show container states on the VM.
@status:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    gcloud compute ssh {{ vm_name }} --zone {{ zone }} --project {{ project }} \
      --tunnel-through-iap --quiet \
      --command "cd {{ app_dir }} && sudo docker compose ps && echo '--- heartbeat:' && cat data/trader_heartbeat 2>/dev/null || true"

# Tail logs of a service (trader|monitor) on the VM.
@logs service="trader":
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(ctx use quantitative-trading --export --confirm 2>/dev/null)"
    gcloud compute ssh {{ vm_name }} --zone {{ zone }} --project {{ project }} \
      --tunnel-through-iap --quiet \
      --command "cd {{ app_dir }} && sudo docker compose logs --tail=100 {{ service }}"
