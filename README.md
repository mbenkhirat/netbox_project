# NetBox Project — NetBox comme source de vérité pour FortiGate

Ce projet utilise **NetBox** comme source de vérité (Source of Truth) pour piloter la configuration et les opérations sur des pare-feu **FortiGate**, via **Ansible**. L'inventaire des équipements n'est pas géré à la main : il est récupéré dynamiquement depuis NetBox à chaque exécution.

## Architecture

```
NetBox (Source of Truth)
      │
      ▼
Inventaire dynamique Ansible (netbox.netbox.nb_inventory)
      │
      ▼
Playbooks Ansible ──► FortiGate (API REST FortiOS)
      │
      ▼
GitHub Actions (CI/CD, runner self-hosted)
```

- **NetBox** héberge les informations de référence : sites, rôles, types d'équipements, IPs.
- **L'inventaire dynamique** (`netbox_inventory.yml`) interroge l'API NetBox à chaque run et génère automatiquement les groupes Ansible (par site, rôle, fabricant, type d'équipement).
- **Les playbooks** consomment cet inventaire pour agir sur les FortiGate réels (lecture de statut, synchronisation de configuration, sauvegarde).
- **GitHub Actions**, sur un runner self-hosted, automatise l'exécution planifiée et à la demande.

## Prérequis

- Python 3.12 (⚠️ Python 3.14 provoque des incompatibilités connues avec `pytz` et le plugin d'inventaire — éviter)
- Ansible + environnement virtuel dédié
- Accès réseau à l'instance NetBox et aux FortiGate cibles
- Un serveur NetBox déjà opérationnel avec :
  - Le(s) FortiGate modélisé(s) (Manufacturer, Device Type, Device, IP primaire)
  - Un token API v2 (Bearer) avec les droits nécessaires
- Un compte API FortiOS dédié sur chaque FortiGate (profil d'accès approprié, token généré via `execute api-user generate-key`)

## Installation

```bash
git clone <repo-url>
cd netbox_project

python3.12 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
ansible-galaxy collection install -r requirements.yml
```

## Gestion des secrets (Ansible Vault)

Les tokens API (NetBox et FortiOS) ne sont **jamais** stockés en clair. Ils sont chiffrés via Ansible Vault.

### Initialisation locale

```bash
openssl rand -base64 32 > .vault_pass
echo ".vault_pass" >> .gitignore
```

### Stockage des secrets

Le token NetBox est chiffré directement dans `netbox_inventory.yml` via le tag YAML `!vault` (nécessaire car les `group_vars` classiques ne se résolvent pas correctement dans le contexte du plugin d'inventaire) :

```bash
ansible-vault encrypt_string 'TON_TOKEN_NETBOX' --name 'netbox_token'
```

Le token FortiOS (et les identifiants SSH si utilisés) sont stockés dans `group_vars/all/vault.yml` :

```bash
ansible-vault edit group_vars/all/vault.yml
```

```yaml
fortios_token: "nbt_xxxxx.xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

`ansible.cfg` référence automatiquement `.vault_pass`, donc aucune option supplémentaire n'est nécessaire lors des exécutions locales.

⚠️ **`.vault_pass` ne doit jamais être commité** — vérifié dans `.gitignore`.

## Structure du projet

```
netbox_project/
├── .github/workflows/         # Pipelines CI/CD GitHub Actions
├── backup_reports/            # Rapports de backup horodatés (généré, non versionné)
├── local_backups/             # Sauvegardes de configuration par device (généré, non versionné)
├── group_vars/all/vault.yml   # Secrets chiffrés (token FortiOS, identifiants SSH)
├── netbox_inventory.yml       # Inventaire dynamique NetBox (token NetBox chiffré inline)
├── ansible.cfg                # Configuration Ansible (vault_password_file, plugins d'inventaire)
├── requirements.txt           # Dépendances Python (pynetbox, etc.)
├── requirements.yml           # Collections Ansible (netbox.netbox, fortinet.fortios)
├── .vault_pass                # Mot de passe du vault (local uniquement, jamais commité)
├── .gitignore
└── *.yml                      # Playbooks (voir ci-dessous)
```

## Inventaire dynamique

```bash
ansible-inventory -i netbox_inventory.yml --graph
```

Groupement automatique par :
- `sites` (ex. `sites_ken`)
- `device_roles` (ex. `device_roles_fw`)
- `manufacturers` (ex. `manufacturers_fortinet`)
- `device_types` (ex. `device_types_fortinet-fg-100f`)

Les playbooks ciblent généralement le groupe `device_roles_fw` pour s'appliquer à tous les FortiGate présents dans NetBox, sans liste statique à maintenir.

## Playbooks disponibles

| Playbook | Rôle |
|---|---|
| `system_status_playbook.yml` | Vérifie la connectivité et récupère le statut système d'un FortiGate (test de connexion) |
| `sync_hostname_playbook.yml` | Aligne le hostname réel du FortiGate sur le nom du device défini dans NetBox (NetBox = source de vérité) |
| `new_backup_playbook.yml` | Sauvegarde multi-device de la configuration FortiGate, avec rétention automatique (90 jours) et rapport horodaté |

Chaque playbook génère un rapport dans `backup_reports/` (pour le backup) résumant le statut par équipement.

### Exécution manuelle

```bash
ansible-playbook -i netbox_inventory.yml new_backup_playbook.yml
```

## Pipeline CI/CD (GitHub Actions)

Le workflow `.github/workflows/backup.yml` automatise le backup :

- **Déclenchement** : manuel (`workflow_dispatch`) ou planifié (cron quotidien)
- **Runner** : self-hosted (réutilise l'environnement Python/Ansible déjà en place sur `~/ansible-projects/venv`)
- **Secret requis** : `ANSIBLE_VAULT_PASSWORD` (contenu de `.vault_pass`, à configurer dans *Settings → Secrets and variables → Actions*)
- **Stockage** : les sauvegardes et rapports sont écrits à un **chemin absolu fixe** sur le serveur (`/home/mbenkhirat/ansible-projects/forti-automation/netbox_project/`), volontairement en dehors du workspace éphémère du runner — `actions/checkout` nettoie ce workspace à chaque run (`git clean -ffdx`), ce qui effacerait les sauvegardes précédentes si elles y étaient stockées.
- Les artefacts de backup sont également uploadés via `actions/upload-artifact` pour consultation depuis l'interface GitHub.

### Configuration du secret GitHub

1. `cat .vault_pass` en local pour récupérer la valeur exacte
2. GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**
3. Nom : `ANSIBLE_VAULT_PASSWORD` — coller la valeur sans espace ni ligne vide ajoutée

## Notes de compatibilité connues

- **Python 3.14** casse le plugin d'inventaire NetBox (`pytz` non résolu) — utiliser Python 3.12.
- Le module `fortios_configuration_fact` interroge l'API **CMDB** ; pour les statuts en temps réel (ex. `system_status`), utiliser `fortios_monitor_fact` (API **Monitor**).
- L'upload de firmware volumineux (>100 Mo) via l'API Monitor FortiOS (`upgrade.system.firmware`, source `upload`) est instable (`Broken pipe`) — privilégier la source `fortiguard` si le FortiGate a un accès Internet sortant, ou un transfert TFTP sinon.
- Les tokens API NetBox v2 utilisent le schéma `Bearer <clé>.<jeton>` ; le connecteur d'inventaire attend un bloc structuré (`token: {type: Bearer, value: ...}`), pas une chaîne brute.

## Sécurité

- Aucun secret n'est stocké en clair dans le dépôt (vérifié via `.gitignore` : `.vault_pass`, `*.env`, `vault.yml` non chiffré, fichiers firmware `.out`).
- Le token FortiOS et le token NetBox sont scindés par usage (Vault inline pour l'inventaire, `group_vars` pour les playbooks).
- Rotation recommandée des tokens API en cas d'exposition accidentelle (ex. collée dans un terminal partagé ou un ticket).