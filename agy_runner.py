import sys
import subprocess
import os

if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        prompt = f.read()
    cmd = ["/home/ubuntu/.local/bin/agy", "-p", prompt, "--effort", "low", "--dangerously-skip-permissions", "--output-format", "json"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    sys.stdout.write(res.stdout)
    try:
        os.remove(sys.argv[1])
    except Exception:
        pass
