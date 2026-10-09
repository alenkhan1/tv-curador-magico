#!/usr/bin/env bash
set -e

REPO_DIR="/home/ubuntu/tv-curador-magico"
cd "$REPO_DIR"

echo "=== [$(date)] Sincronizando con origin/main ==="
git rebase --abort 2>/dev/null || true
git merge --abort 2>/dev/null || true
git checkout -- . 2>/dev/null || true
git clean -fd 2>/dev/null || true
git fetch origin main
git reset --hard origin/main

echo "=== [$(date)] Ejecutando Curador Deportivo Limpio ==="
export PATH="$HOME/.local/bin:$PATH"
python3 curador_eventos.py

echo "=== [$(date)] Verificando integridad de eventos_hoy.json ==="
python3 -c "
import json
with open('eventos_hoy.json', 'r', encoding='utf-8') as f:
    d = json.load(f)
evs = d.get('eventos', [])
ids = [e['id'] for e in evs]
assert len(ids) == len(set(ids)), 'ERROR: Existen IDs duplicados!'
print(f'OK: {len(evs)} eventos verificados, 0 colisiones de ID')
"

echo "=== [$(date)] Publicando cambios a GitHub ==="
git add -u 2>/dev/null || true
if git status --porcelain | grep -q .; then
    git commit -m "Cartelera deportiva actualizada $(date -u '+%Y-%m-%d %H:%M UTC')"
    git push origin main || {
        echo "Push fallo, sincronizando con rebase..."
        git fetch origin main
        git pull --rebase -X theirs origin main
        git push origin main
    }
    echo "=== [$(date)] Cambios publicados con exito en GitHub ==="
else
    echo "=== [$(date)] Sin cambios nuevos para publicar ==="
fi
