# Claude Kanban

Kanban local multi-projets qui pilote des instances headless de Claude Code pour
exécuter des tickets de façon autonome, avec des points de contrôle humains
(validation fonctionnelle, commit, push).

Voir `/Users/tibo/.claude/plans/eager-hugging-crane.md` pour le plan technique complet.

## Démarrer

```bash
./run.sh
```

L'app écoute sur `http://127.0.0.1:8787`, en local uniquement.

## Créer un ticket

Depuis Claude Desktop ou Claude Code, ouvre une session dans le dossier du projet
concerné et déclenche la commande `/kanban-ticket` (ou demande simplement à créer
un ticket). L'IA analysera le code, posera des questions, écrira un plan, puis
créera le ticket dans le tableau.

## Colonnes

À faire → En cours → Stand by (question) → À valider → À committer → Terminé
