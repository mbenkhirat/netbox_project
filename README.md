# NetBox as SoT — Dynamic Ansible Inventory for Network Automation

This project demonstrates using **NetBox as the Source of Truth (SoT)** to drive network automation via **Ansible**, through a **dynamic inventory** mechanism. Rather than maintaining a static device list, Ansible queries NetBox on every run to pull the up-to-date inventory — devices, sites, roles — directly from the authoritative network database. The current implementation targets **FortiGate** firewalls via the FortiOS REST API, but the architecture (NetBox → dynamic inventory → playbooks) is designed to extend to other device types.

## Architecture

```
NetBox (Source of Truth)
      │
      ▼
Ansible dynamic inventory (netbox.netbox.nb_inventory)
      │
      ▼
Ansible playbooks ──► FortiGate (FortiOS REST API)
      │
      ▼
GitHub Actions (CI/CD, self-hosted runner)
```

- **NetBox** holds the reference data: sites, roles, device types, IPs.
- **The dynamic inventory** (`netbox_inventory.yml`) queries the NetBox API on every run and automatically generates Ansible groups (by site, role, manufacturer, device type).
- **Playbooks** consume this inventory to act on real FortiGate devices (status checks, config synchronization, backups).
- **GitHub Actions**, on a self-hosted runner, automates scheduled and on-demand execution.

## Prerequisites

- Python 3.12 (⚠️ Python 3.14 causes known incompatibilities with `pytz` and the inventory plugin — avoid it)
- Ansible + a dedicated virtual environment
- Network access to the NetBox instance and target FortiGate devices
- A running NetBox instance with:
  - The FortiGate(s) already modeled (Manufacturer, Device Type, Device, primary IP)
  - A v2 (Bearer) API token with the required permissions
- A dedicated FortiOS API account on each FortiGate (appropriate access profile, token generated via `execute api-user generate-key`)

## Installation

```bash
git clone https://github.com/mbenkhirat/netbox_project.git
cd netbox_project

python3.12 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
ansible-galaxy collection install -r requirements.yml
```

## Secrets Management (Ansible Vault)

API tokens (NetBox and FortiOS) are **never** stored in plaintext. They are encrypted via Ansible Vault.

### Local initialization

```bash
openssl rand -base64 32 > .vault_pass
echo ".vault_pass" >> .gitignore
```

### Storing secrets

The NetBox token is encrypted directly inside `netbox_inventory.yml` using the YAML `!vault` tag (necessary because standard `group_vars` don't resolve correctly in the inventory plugin's execution context):

```bash
ansible-vault encrypt_string 'YOUR_NETBOX_TOKEN' --name 'netbox_token'
```

The FortiOS token (and SSH credentials, if used) are stored in `group_vars/all/vault.yml`:

```bash
ansible-vault edit group_vars/all/vault.yml
```

```yaml
fortios_token: "nbt_xxxxx.xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

`ansible.cfg` automatically references `.vault_pass`, so no extra flags are needed for local runs.

⚠️ **`.vault_pass` must never be committed** — verified in `.gitignore`.

## Project Structure

```
netbox_project/
├── .github/workflows/          # GitHub Actions CI/CD pipelines
│   ├── backup.yml              # FortiGate backup (manual + scheduled)
│   └── sync_hostname.yml       # Hostname sync (manual only)
├── backup_reports/             # Timestamped backup reports (generated, not versioned)
├── local_backups/              # Per-device configuration backups (generated, not versioned)
├── sync_hostname_reports/      # Timestamped hostname sync reports (generated, not versioned)
├── group_vars/all/vault.yml    # Encrypted secrets (FortiOS token, SSH credentials)
├── netbox_inventory.yml        # NetBox dynamic inventory (NetBox token encrypted inline)
├── ansible.cfg                 # Ansible configuration (vault_password_file, inventory plugins)
├── requirements.txt            # Python dependencies (pynetbox, etc.)
├── requirements.yml            # Ansible collections (netbox.netbox, fortinet.fortios)
├── .vault_pass                 # Vault password (local only, never committed)
├── .gitignore
├── backup_playbook.yml
├── sync_hostname_playbook.yml
└── system_status_playbook.yml
```

## Dynamic Inventory

```bash
ansible-inventory -i netbox_inventory.yml --graph
```

Automatic grouping by:
- `sites` (e.g. `sites_ken`)
- `device_roles` (e.g. `device_roles_fw`)
- `manufacturers` (e.g. `manufacturers_fortinet`)
- `device_types` (e.g. `device_types_fortinet-fg-100f`)

Playbooks typically target the `device_roles_fw` group to act on every FortiGate present in NetBox, with no static list to maintain.

## Available Playbooks

| Playbook | Purpose |
|---|---|
| `system_status_playbook.yml` | Checks connectivity and retrieves system status from a FortiGate (connection test) |
| `sync_hostname_playbook.yml` | Aligns the actual FortiGate hostname with the device name defined in NetBox (NetBox as source of truth). Tolerant of unreachable devices: the report shows "🔴 Unreachable" without failing the run |
| `backup_playbook.yml` | Multi-device FortiGate configuration backup, with automatic retention (90 days) and a timestamped report |

Both `backup_playbook.yml` and `sync_hostname_playbook.yml` generate a timestamped report in `backup_reports/` summarizing the status per device.

### Manual execution

```bash
ansible-playbook -i netbox_inventory.yml backup_playbook.yml
ansible-playbook -i netbox_inventory.yml sync_hostname_playbook.yml
ansible-playbook -i netbox_inventory.yml system_status_playbook.yml
```

## CI/CD Pipelines (GitHub Actions)

| Workflow | Playbook run | Trigger |
|---|---|---|
| `.github/workflows/backup.yml` | `backup_playbook.yml` | Manual (`workflow_dispatch`) + scheduled (daily cron) |
| `.github/workflows/sync_hostname.yml` | `sync_hostname_playbook.yml` | Manual only (`workflow_dispatch`) |

Shared characteristics of both pipelines:
- **Runner**: self-hosted (reuses the existing Python/Ansible environment at `~/ansible-projects/venv`)
- **Required secret**: `ANSIBLE_VAULT_PASSWORD` (content of `.vault_pass`, configured under *Settings → Secrets and variables → Actions*), written to a temporary file at job start and removed at the end of the run
- **Storage**: backups and reports are written to a **fixed absolute path** on the server (`/home/mbenkhirat/ansible-projects/forti-automation/netbox_project/`), deliberately outside the runner's ephemeral workspace — `actions/checkout` wipes that workspace on every run (`git clean -ffdx`), which would delete previous reports if stored there
- Reports/artifacts are also uploaded via `actions/upload-artifact` for review from the GitHub interface

### Configuring the GitHub secret

1. Run `cat .vault_pass` locally to get the exact value
2. GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**
3. Name: `ANSIBLE_VAULT_PASSWORD` — paste the value with no extra whitespace or blank lines

## Known Compatibility Notes

- **Python 3.14** breaks the NetBox inventory plugin (`pytz` fails to resolve) — use Python 3.12.
- The `fortios_configuration_fact` module queries the **CMDB** API; for real-time status (e.g. `system_status`), use `fortios_monitor_fact` (**Monitor** API) instead.
- Uploading large firmware files (>100 MB) via the FortiOS Monitor API (`upgrade.system.firmware`, `source: upload`) is unreliable (`Broken pipe`) — prefer the `fortiguard` source if the FortiGate has outbound Internet access, or a TFTP transfer otherwise.
- NetBox v2 API tokens use the `Bearer <key>.<token>` scheme; the inventory connector expects a structured block (`token: {type: Bearer, value: ...}`), not a raw string.

## Security

- No secret is stored in plaintext in the repository (enforced via `.gitignore`: `.vault_pass`, `*.env`, unencrypted `vault.yml`, `.out` firmware files).
- FortiOS and NetBox tokens are split by usage (inline Vault for the inventory, `group_vars` for playbooks).
- Rotate API tokens if accidentally exposed (e.g. pasted into a shared terminal or a ticket).
