# Rapport d'audit des règles firewall — FG01

- Généré le : 2026-10-01T12:19:18.714327+00:00
- Runs analysés dans l'historique : 2
- Seuil d'âge minimum : 30 jours
- Seuil de runs consécutifs à zéro hit : 4

## Règles candidates au nettoyage (jamais utilisées)

Aucune règle candidate pour le moment — soit tout est utilisé, soit l'historique est encore trop court (besoin d'au moins 4 runs sans reset entre-temps).

## Règles désactivées toujours présentes dans la config

Aucune règle désactivée trouvée.

## Note méthodologique

Un reset de compteur (reboot, diagnostic reset, etc.) remet `hit_count`/`bytes` à zéro. Ce rapport détecte ces resets par policy (une baisse de `hit_count` par rapport au run précédent) et réinitialise le compteur de runs consécutifs à ce moment-là, pour éviter de flaguer à tort une règle utile juste après un redémarrage.
