import sys
import subprocess
import os

if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
    with open(sys.argv[1], 'r', encoding='utf-8') as pf:
        prompt = pf.read()
    cmd = [
        '/home/ubuntu/.local/bin/agy',
        '-p', prompt,
        '--effort', 'low',
        '--dangerously-skip-permissions'
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    sys.stdout.write(res.stdout)
    try:
        os.remove(sys.argv[1])
    except Exception:
        pass
