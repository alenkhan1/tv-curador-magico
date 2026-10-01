#!/usr/bin/env bash
set -e

REPO_DIR="/home/ubuntu/tv-curador-magico"
cd "$REPO_DIR"

echo "=== [$(date)] Actualizando codigo desde origin/main ==="
git pull --rebase origin main || true

echo "=== [$(date)] Ejecutando Curador Deportivo Limpio ==="
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
git add eventos_hoy.json eventos_descartados.json meta_curador.json cache_logos.json || true
if git status --porcelain | grep -q .; then
    git commit -m "Cartelera deportiva actualizada $(date -u '+%Y-%m-%d %H:%M UTC')"
    git push origin main
    echo "=== [$(date)] Cambios publicados con exito en GitHub ==="
else
    echo "=== [$(date)] Sin cambios nuevos para publicar ==="
fi
