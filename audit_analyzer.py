#!/usr/bin/env python3
"""
audit_analyzer.py

Analyse l'historique des snapshots de règles firewall FortiGate (hit_count/bytes)
pour identifier les règles jamais utilisées, en tenant compte des resets de
compteurs (reboot, diagnostic reset, etc.) qui invalident un streak en cours.
La détection de reset se fait par policy, en comparant son hit_count au run
précédent : une baisse signale que le compteur a été remis à zéro.

Usage:
    python3 audit_analyzer.py \
        --history-dir reports/history/fw01 \
        --names-file reports/policy_names_fw01.json \
        --min-age-days 30 \
        --min-consecutive 4 \
        --output reports/audit_report_fw01.md \
        --device-name fw01
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone


def load_history(history_dir):
    """Charge tous les snapshots run_*.json, triés par timestamp croissant."""
    files = sorted(glob.glob(os.path.join(history_dir, "run_*.json")))
    runs = []
    for f in files:
        try:
            with open(f, "r") as fh:
                data = json.load(fh)
                data["_file"] = os.path.basename(f)
                runs.append(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: skipping unreadable snapshot {f}: {e}", file=sys.stderr)
    return runs


def load_names(names_file):
    """Charge la config des policies (nom, src/dst, service, status) pour le rapport."""
    if not os.path.exists(names_file):
        return {}
    with open(names_file, "r") as fh:
        entries = json.load(fh)
    return {str(e.get("policyid")): e for e in entries}


def build_policy_timelines(runs):
    """
    Reconstruit, pour chaque policyid, la séquence de hit_count à travers les runs.
    Retourne: dict policyid -> list of {timestamp, hit_count, bytes, creation_time}
    """
    timelines = {}

    for run in runs:
        for policy in run.get("policies", []):
            pid = str(policy.get("policyid"))
            if pid == "0":
                # policyid 0 = règle implicite "deny all" en fin de liste, on l'ignore
                continue
            timelines.setdefault(pid, []).append({
                "timestamp": run.get("timestamp"),
                "hit_count": int(policy.get("hit_count", 0)),
                "bytes": int(policy.get("bytes", 0)),
                "creation_time": policy.get("creation_time"),
            })

    return timelines


def compute_zero_streak(entries):
    """
    Calcule la longueur du streak courant de hit_count==0, en repartant de zéro
    dès qu'une baisse de hit_count est détectée par rapport au run précédent
    (signe que le compteur a été remis à zéro — reboot, diag reset, etc.).
    """
    streak = 0
    first_zero_ts = None
    prev_hit_count = None

    for entry in entries:
        hit_count = entry["hit_count"]

        if prev_hit_count is not None and hit_count < prev_hit_count:
            # reset détecté sur cette policy précise : le streak en cours
            # n'est plus significatif, on repart de zéro
            streak = 0
            first_zero_ts = None

        if hit_count == 0:
            streak += 1
            if first_zero_ts is None:
                first_zero_ts = entry["timestamp"]
        else:
            streak = 0
            first_zero_ts = None

        prev_hit_count = hit_count

    return streak, first_zero_ts


def age_days(creation_time):
    if not creation_time:
        return None
    try:
        created = datetime.fromtimestamp(int(creation_time), tz=timezone.utc)
        return (datetime.now(timezone.utc) - created).days
    except (ValueError, TypeError, OSError):
        return None


def generate_report(timelines, names, min_age_days, min_consecutive, device_name, total_runs):
    candidates = []
    disabled_still_present = []

    for pid, entries in timelines.items():
        if not entries:
            continue
        latest = entries[-1]
        streak, first_zero_ts = compute_zero_streak(entries)
        age = age_days(latest.get("creation_time"))
        meta = names.get(pid, {})
        name = meta.get("name", f"policy-{pid}")
        status = meta.get("status", "unknown")
        srcaddr = ",".join(a.get("name", "") for a in meta.get("srcaddr", [])) if isinstance(meta.get("srcaddr"), list) else meta.get("srcaddr", "")
        dstaddr = ",".join(a.get("name", "") for a in meta.get("dstaddr", [])) if isinstance(meta.get("dstaddr"), list) else meta.get("dstaddr", "")
        service = ",".join(s.get("name", "") for s in meta.get("service", [])) if isinstance(meta.get("service"), list) else meta.get("service", "")

        if status == "disable":
            disabled_still_present.append({
                "policyid": pid, "name": name, "age_days": age,
            })

        is_candidate = (
            streak >= min_consecutive
            and (age is None or age >= min_age_days)
            and status != "disable"  # déjà couvert dans l'autre liste
        )
        if is_candidate:
            candidates.append({
                "policyid": pid,
                "name": name,
                "age_days": age,
                "zero_streak_runs": streak,
                "zero_since": first_zero_ts,
                "srcaddr": srcaddr,
                "dstaddr": dstaddr,
                "service": service,
            })

    candidates.sort(key=lambda c: c["zero_streak_runs"], reverse=True)

    lines = []
    lines.append(f"# Rapport d'audit des règles firewall — {device_name}")
    lines.append("")
    lines.append(f"- Généré le : {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- Runs analysés dans l'historique : {total_runs}")
    lines.append(f"- Seuil d'âge minimum : {min_age_days} jours")
    lines.append(f"- Seuil de runs consécutifs à zéro hit : {min_consecutive}")
    lines.append("")

    lines.append("## Règles candidates au nettoyage (jamais utilisées)")
    lines.append("")
    if not candidates:
        lines.append("Aucune règle candidate pour le moment — soit tout est utilisé, "
                      "soit l'historique est encore trop court (besoin d'au moins "
                      f"{min_consecutive} runs sans reset entre-temps).")
    else:
        lines.append("| Policy ID | Nom | Âge (j) | Runs consécutifs à 0 hit | Zéro depuis | Source | Destination | Service |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for c in candidates:
            lines.append(
                f"| {c['policyid']} | {c['name']} | {c['age_days']} | "
                f"{c['zero_streak_runs']} | {c['zero_since']} | {c['srcaddr']} | "
                f"{c['dstaddr']} | {c['service']} |"
            )
    lines.append("")

    lines.append("## Règles désactivées toujours présentes dans la config")
    lines.append("")
    if not disabled_still_present:
        lines.append("Aucune règle désactivée trouvée.")
    else:
        lines.append("| Policy ID | Nom | Âge (j) |")
        lines.append("|---|---|---|")
        for d in disabled_still_present:
            lines.append(f"| {d['policyid']} | {d['name']} | {d['age_days']} |")
    lines.append("")

    lines.append("## Note méthodologique")
    lines.append("")
    lines.append(
        "Un reset de compteur (reboot, diagnostic reset, etc.) remet `hit_count`/"
        "`bytes` à zéro. Ce rapport détecte ces resets par policy (une baisse de "
        "`hit_count` par rapport au run précédent) et réinitialise le compteur de "
        "runs consécutifs à ce moment-là, pour éviter de flaguer à tort une règle "
        "utile juste après un redémarrage."
    )
    lines.append("")

    return "\n".join(lines), len(candidates), len(disabled_still_present)


def main():
    parser = argparse.ArgumentParser(description="Analyse l'historique d'audit des règles FortiGate")
    parser.add_argument("--history-dir", required=True)
    parser.add_argument("--names-file", required=True)
    parser.add_argument("--min-age-days", type=int, default=30)
    parser.add_argument("--min-consecutive", type=int, default=4)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device-name", required=True)
    args = parser.parse_args()

    runs = load_history(args.history_dir)
    if not runs:
        print("Aucun snapshot trouvé dans l'historique — rien à analyser pour l'instant.")
        sys.exit(0)

    names = load_names(args.names_file)
    timelines = build_policy_timelines(runs)
    report, n_candidates, n_disabled = generate_report(
        timelines, names, args.min_age_days, args.min_consecutive,
        args.device_name, len(runs)
    )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as fh:
        fh.write(report)

    print(f"Runs analysés : {len(runs)}")
    print(f"Règles candidates au nettoyage : {n_candidates}")
    print(f"Règles désactivées toujours présentes : {n_disabled}")
    print(f"Rapport écrit dans : {args.output}")


if __name__ == "__main__":
    main()