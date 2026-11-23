#!/usr/bin/env bash
# Crée le dépôt GitHub, pousse le code et active GitHub Pages.
# Prérequis : git + GitHub CLI (https://cli.github.com), puis `gh auth login`.
set -e
NOM="${1:-roboclean-arm-v1}"
git init -b main
git add .
git commit -m "RoboClean Arm-V1 : poste de commande (Python + web)"
gh repo create "$NOM" --public --source=. --push
gh api -X POST "repos/{owner}/{repo}/pages" -f build_type=workflow || true
echo "Dépôt créé. La version web sera en ligne après le premier workflow « Publier la version web »."
echo "Pour publier des exécutables : git tag v1.0.0 && git push origin v1.0.0"
