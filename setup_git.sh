#!/usr/bin/env bash
# Git Bash-тан іске қосу: bash setup_git.sh
cd "$(dirname "$0")"

if ! command -v git >/dev/null 2>&1; then
  echo "Git tabylmady. Ornatyngyz: https://git-scm.com/download/win"
  exit 1
fi

if [ ! -d .git ]; then
  git init
  git branch -M main
fi

git add -A
git status

echo ""
read -rp "Commit habarlama [ScalpXau bot initial]: " MSG
MSG=${MSG:-ScalpXau bot initial}
git commit -m "$MSG"

echo ""
echo "OK. Git repo daiyn: $(pwd)"
echo ""
echo "GitHub private repo jasap:"
echo "  git remote add origin https://github.com/SIZIN_USER/ScalpXau.git"
echo "  git push -u origin main"
