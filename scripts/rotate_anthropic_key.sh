#!/usr/bin/env bash
# Rotate the Anthropic API key used by the demonstrator:
#   1. checks that the key currently in .env has been revoked,
#   2. asks for the new key (hidden input), checks it works,
#   3. writes .env (mode 600, git-ignored) and updates the GitHub Actions secret.
# The key is never printed.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="djindetoss/pk-analysis-poc"
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3

check_key() {  # prints ok | revoked | error:<msg>
  ANTHROPIC_API_KEY="$1" "$PY" - <<'PYEOF'
import anthropic
try:
    anthropic.Anthropic(max_retries=0).models.retrieve("claude-opus-5-5")
    print("ok")
except anthropic.AuthenticationError:
    print("revoked")
except Exception as e:
    print(f"error:{type(e).__name__}: {e}")
PYEOF
}

if [ -f .env ]; then
  OLD=$(sed -n 's/^ANTHROPIC_API_KEY=//p' .env)
  if [ -n "$OLD" ]; then
    state=$(check_key "$OLD")
    if [ "$state" = "ok" ]; then
      echo "La clé actuelle de .env fonctionne encore : révoque-la d'abord sur https://console.anthropic.com/settings/keys puis relance ce script."
      exit 1
    fi
    echo "✓ Ancienne clé : $state"
  fi
  unset OLD
fi

read -rs -p "Nouvelle clé Anthropic (collée une seule fois) : " KEY; echo
KEY=$(printf '%s' "$KEY" | tr -d '[:space:]')
n=$(printf '%s' "$KEY" | grep -o 'sk-ant-' | wc -l | tr -d ' ')
if [ "${KEY:0:7}" != "sk-ant-" ] || [ "$n" != "1" ]; then
  echo "Format inattendu (la clé doit commencer par sk-ant- et être collée une seule fois). Rien n'a été modifié."
  exit 1
fi
state=$(check_key "$KEY")
if [ "$state" != "ok" ]; then
  echo "La nouvelle clé ne fonctionne pas ($state). Rien n'a été modifié."
  exit 1
fi
echo "✓ Nouvelle clé valide"

umask 077
printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" > .env
chmod 600 .env
unset KEY
echo "✓ .env mis à jour"
gh secret set -f .env -R "$REPO"
echo "Terminé."
