# VIGIE — Spécification cible

**Agent d'observabilité branchable — technique & fonctionnelle**
Statut : Spécification cible (produit) | Réfère à la roadmap V1 + V2 | Juillet 2026

> Ce document décrit l'état **cible** du produit, au-delà du prototype V0. Il sert de référence pour le chiffrage, le développement V1/V2, et la Fiche PU officielle. Chaque section indique le delta par rapport à la V0 déjà maquettée.

---

## 1. Vision produit

VIGIE cible un produit vendable, packagé, installable en moins d'une journée sur n'importe quel projet web en production, sans modification de son code, et capable de :

1. **Se découvrir lui-même** sur un nouveau projet (formats de logs, services actifs, schéma de base) plutôt que d'être configuré manuellement.
2. **Apprendre le vocabulaire métier du projet** au lieu de s'appuyer sur des mots-clés figés.
3. **Alerter en langage naturel** de façon proactive, pas seulement répondre à la demande.
4. **S'intégrer à l'écosystème agentique ETECH** comme un capteur consommable par d'autres agents (via MCP).
5. **Offrir une profondeur de tracing optionnelle** pour les clients qui acceptent une instrumentation légère.

Principe directeur conservé de la V0 : **le LLM n'analyse jamais le flux brut**. Le filtrage/agrégation reste assuré par des règles classiques ; le LLM intervient sur anomalies, requêtes explicites, et synthèses périodiques.

---

## 2. Architecture cible

```
┌──────────────────────────────────────────────────────────────────────┐
│ Projet(s) observés (multi-tenant)                                    │
│   logs fichiers / conteneurs / syslog ── métriques OS ── (option)     │
│   SDK OTel léger ── (option) endpoint /metrics applicatif             │
└───────────────┬────────────────────────────────────────────────────--┘
                │
        ┌───────▼────────┐
        │ Couche Discovery │  ← NOUVEAU (V1)
        │ (scan formats,   │
        │  services, DB)   │
        └───────┬────────┘
                │ génère
        ┌───────▼────────┐
        │ Config Vector    │  générée automatiquement, éditable
        │ auto-générée     │
        └───────┬────────┘
                │
    ┌───────────▼────────────┐
    │ Collecte & normalisation │ (Vector — inchangé structurellement)
    └───────────┬────────────┘
                │
     ┌──────────┼──────────────┐
     ▼          ▼               ▼
   Loki     Prometheus      Tempo/Jaeger        ← NOUVEAU (V2, si SDK OTel actif)
  (logs)   (métriques)       (traces)
     │          │               │
     └──────────┼───────────────┘
                ▼
     ┌────────────────────────┐
     │  Moteur de classification│  ← ÉVOLUTION (V1)
     │  métier appris (pas figé)│
     └───────────┬────────────┘
                 │
     ┌───────────▼─────────────────────────┐
     │  Agent VIGIE (FastAPI)                │
     │  - Triage continu (Haiku)  ← NOUVEAU  │
     │  - Diagnostic (Sonnet)     ← V0       │
     │  - Serveur MCP             ← NOUVEAU (V2)
     └───────────┬────────────┬─────────────┘
                 │            │
        ┌────────▼───┐   ┌────▼────────────┐
        │  Grafana     │   │ Alerting sortant │  ← NOUVEAU (V1)
        │  (dashboards)│   │ Slack / email    │
        └──────────────┘   │ langage naturel  │
                            └──────────────────┘
                 │
        ┌────────▼─────────────────┐
        │ Consommateurs MCP externes │  ← NOUVEAU (V2)
        │ (autres agents ETECH)      │
        └────────────────────────────┘
```

---

## 3. Delta par composant

### 3.1 Couche Discovery (nouveau — V1)

**But** : éliminer la configuration manuelle de `vector.toml`, seul point de friction restant de la V0.

**Fonctionnement cible** :
- Scan initial du système hôte/conteneurs : détection des chemins de logs probables (`/var/log/*`, répertoires `storage/logs`, `var/log` connus des frameworks courants), des ports ouverts, des services actifs.
- Échantillonnage des premières lignes de chaque source de log détectée, envoyé au LLM pour inférer : format (JSON structuré, texte libre, format framework connu), champs disponibles, niveau de verbosité.
- Génération automatique d'une proposition de `vector.toml`, présentée à l'opérateur pour validation avant activation (jamais d'auto-application silencieuse sur un système en production).

**Interface** : commande `vigie discover --target <host_ou_conteneur>` → rapport de découverte + fichier de config proposé.

**Garde-fou** : le scan est read-only, ne modifie rien sur le système observé, et le fichier généré reste éditable manuellement.

### 3.2 Classification métier apprise (évolution — V1)

**But** : remplacer l'heuristique par mots-clés figés de la V0 par un classement qui s'adapte au vocabulaire réel du projet.

**Fonctionnement cible** :
- Phase d'apprentissage (une fois par projet, ou en continu à faible fréquence) : le LLM analyse un échantillon de logs sur quelques jours et propose une taxonomie d'événements métier candidats (ex. pour un projet GPAO : `ordre_fabrication_créé`, `ordre_fabrication_clôturé`, `rupture_stock_détectée`).
- Validation humaine de la taxonomie proposée (atelier de 30 min avec le client, dans l'esprit de l'atelier déjà prévu en Setup).
- Une fois validée, la classification devient une règle déterministe (regex/patterns dérivés) exécutée par Vector — le LLM n'est donc pas sollicité à chaque ligne de log, seulement lors de la phase d'apprentissage et de ses révisions périodiques.

**Sortie** : un fichier de mapping métier versionné, propre à chaque projet, remplaçant les mots-clés génériques `stream_type`.

### 3.3 Triage continu et alerting proactif (nouveau — V1)

**But** : passer d'un agent purement réactif (répond quand on l'interroge) à un agent qui signale de lui-même les anomalies.

**Fonctionnement cible** :
- Un job périodique (ex. toutes les 5-15 min) interroge Loki/Prometheus avec des règles de seuils simples (taux d'erreur, latence, saturation ressources) — **coût nul**, pas de LLM à ce stade.
- Si un seuil est franchi, `MODEL_TRIAGE` (Haiku) est appelé pour une première qualification rapide et peu coûteuse : s'agit-il d'un bruit connu ou d'une anomalie plausible ?
- Si qualifié comme anomalie plausible, escalade vers `MODEL_DIAGNOSTIC` (Sonnet) qui mène l'investigation complète (boucle Plan-Exécute-Vérifie déjà en place en V0) et rédige un message d'alerte en langage naturel.
- Envoi vers Slack/email via webhook configurable, avec limitation de fréquence (anti-spam d'alertes : pas plus d'une alerte par type d'anomalie par fenêtre de temps configurable).

**Nouveaux endpoints** :
| Endpoint | Méthode | Fonction |
|---|---|---|
| `/alerts/config` | POST/GET | Configuration des seuils et des canaux de sortie |
| `/alerts/history` | GET | Historique des alertes émises |

### 3.4 SDK de tracing optionnel (nouveau — V2)

**But** : offrir une profondeur de diagnostic supérieure (quelle fonction est lente, pas seulement quelle requête HTTP) pour les clients qui acceptent une instrumentation minimale.

**Fonctionnement cible** :
- SDK fin basé sur OpenTelemetry, un import + quelques lignes d'initialisation par langage cible (PHP/Symfony en priorité vu le parc ETECH, puis Node).
- Auto-instrumentation des frameworks courants (requêtes HTTP entrantes/sortantes, requêtes SQL) sans changement du code métier.
- Export vers un collecteur de traces self-hosted (Tempo ou Jaeger), corrélé avec les logs existants via un identifiant de corrélation partagé (trace_id injecté dans les logs).
- Reste strictement **optionnel** : la promesse "zéro code" de la V0/V1 n'est jamais remise en cause ; le SDK est un upsell, pas un prérequis.

### 3.5 Serveur MCP (nouveau — V2)

**But** : exposer VIGIE comme un outil consommable par d'autres agents de l'écosystème ETECH (proto-factory, agents métier futurs), pas seulement par un humain via chat.

**Outils MCP cibles** :

| Outil MCP | Description |
|---|---|
| `get_project_health` | Retourne un état de santé synthétique (technique + métier) sur une fenêtre donnée |
| `query_incidents` | Liste les anomalies détectées sur une période, avec statut (résolu, en cours, ignoré) |
| `get_business_kpis` | Retourne les indicateurs métier définis pour le projet (volumes, taux d'échec, délais) |
| `explain_anomaly` | Lance une investigation à la demande d'un autre agent, retourne un diagnostic structuré |

**Authentification** : jeton par projet/tenant, scellé au niveau du serveur MCP — un agent externe ne peut interroger que les projets pour lesquels il est habilité.

### 3.6 Multi-tenant réel (évolution — V1/V2)

**But** : dépasser le tag `projet` constant de la V0 pour permettre à une seule instance VIGIE de superviser plusieurs projets clients de façon cloisonnée.

**Fonctionnement cible** :
- Chaque projet observé obtient un identifiant de tenant propre, propagé à travers Vector, Loki, Prometheus (labels), et l'agent (scoping des requêtes et des réponses).
- Isolation stricte : aucune requête ou réponse de l'agent ne peut mélanger les données de deux tenants, y compris dans les rapports et l'historique d'alertes.
- Dashboards Grafana organisés par dossier/tenant avec permissions dédiées.

---

## 4. Modèle de données cible

| Entité | Description | Portée |
|---|---|---|
| `tenant` | Un projet/client observé | V1/V2 |
| `log_event` | Ligne de log normalisée, avec `level`, `stream_type`, `tenant_id`, `correlation_id` (si SDK actif) | V0 → étendu |
| `business_event` | Événement métier classifié selon la taxonomie apprise et validée | V1 |
| `metric_sample` | Point de métrique système ou applicatif | V0 → étendu |
| `trace` | Trace distribuée (si SDK OTel actif) | V2 |
| `anomaly` | Anomalie détectée, avec statut, diagnostic associé, historique de résolution | V1 |
| `alert_rule` | Règle de seuil configurée par tenant | V1 |

---

## 5. Exigences non fonctionnelles cibles

| Exigence | Cible | Rationale |
|---|---|---|
| Temps d'installation | < 1 jour par nouveau projet (Discovery incluse) | Condition de viabilité commerciale (Setup Fee court) |
| Coût LLM par projet/mois | Plafonné et prévisible (budget configurable avec arrêt automatique en cas de dépassement) | Éviter la dérive de coût identifiée comme risque |
| Souveraineté des données | 100 % self-hosted, aucune donnée métier brute envoyée hors infrastructure client (hors appels LLM anonymisés) | Argument de différenciation vs SaaS établis |
| Isolation multi-tenant | Aucune fuite de données entre projets, y compris dans les logs de l'agent lui-même | Prérequis avant toute offre à plusieurs clients simultanément |
| Disponibilité de l'agent | Dégradation gracieuse : si l'agent est indisponible, la collecte (Vector/Loki/Prometheus/Grafana) continue de fonctionner indépendamment | L'observabilité de base ne doit jamais dépendre de la disponibilité du LLM |
| Réversibilité | Désinstallation complète possible sans laisser de trace dans le projet observé (aucune modification de code à annuler) | Cohérence avec la promesse "zéro code" |

---

## 6. Interfaces cibles (résumé)

| Interface | V0 | V1 | V2 |
|---|---|---|---|
| `/ask` (diagnostic conversationnel) | ✅ | ✅ | ✅ |
| `/report/daily` | ✅ | ✅ (enrichi KPIs métier appris) | ✅ |
| `vigie discover` (CLI/endpoint découverte) | — | ✅ | ✅ |
| `/alerts/config`, `/alerts/history` | — | ✅ | ✅ |
| Serveur MCP (`get_project_health`, etc.) | — | — | ✅ |
| SDK OTel (tracing) | — | — | ✅ (optionnel) |
| Multi-tenant (scoping par projet) | — | partiel | ✅ complet |

---

## 7. Critères de sortie (definition of done) par version

**V1 — prêt à vendre** :
- Discovery fonctionnelle sur au moins 3 stacks différentes du parc ETECH (ex. Symfony, Laravel, Node)
- Taxonomie métier apprise validée sur au moins 2 projets pilotes réels
- Alerting proactif testé en conditions réelles sur un projet TMA, avec un taux de faux positifs jugé acceptable par l'équipe qui les reçoit
- Coût LLM mensuel mesuré et documenté sur un projet pilote, comparé à l'estimation de cadrage

**V2 — plateforme** :
- SDK OTel déployé et validé sur au moins un projet volontaire, sans régression fonctionnelle constatée
- Serveur MCP interrogé avec succès par au moins un autre agent de l'écosystème ETECH (ex. proto-factory)
- Isolation multi-tenant vérifiée par un test explicite de non-fuite de données entre deux tenants

---

## 8. Risques spécifiques à la cible (au-delà de ceux déjà identifiés en V0)

| Risque | Mitigation cible |
|---|---|
| La Discovery propose une config incorrecte sur un système sensible | Toujours en mode proposition + validation humaine, jamais d'auto-application ; scan strictement read-only |
| La taxonomie métier apprise dérive ou devient incohérente dans le temps | Révision périodique programmée, versionnée, avec diff visible avant application |
| Faux positifs d'alerting qui lassent les équipes | Triage Haiku avant escalade Sonnet, limitation de fréquence, tableau de bord de qualité des alertes (taux d'acceptation/rejet par l'équipe) |
| Le SDK OTel optionnel crée une dépendance perçue comme du "vrai code à maintenir" | Toujours positionné comme upsell distinct, jamais mélangé au discours "zéro code" de l'offre de base |
| Fuite de données entre tenants dans un déploiement mutualisé | Tests d'isolation obligatoires avant toute offre multi-client sur une même instance ; option instance dédiée par client si l'isolation ne peut être garantie à 100 % |

---

## 9. Ce qui ne change pas par rapport à la V0

Pour éviter toute ambiguïté lors du chiffrage V1/V2, ces principes fondateurs de la V0 restent des invariants du produit cible :

- Zéro modification du code applicatif du projet observé (le SDK OTel reste une option, jamais un prérequis)
- Le LLM n'analyse jamais le flux brut — filtrage/agrégation par règles classiques en amont
- Déploiement self-hosted par défaut
- Boucle agentique Plan-Exécute-Vérifie comme méthode de diagnostic
- Distinction stricte faits observés / hypothèses dans toutes les sorties de l'agent
