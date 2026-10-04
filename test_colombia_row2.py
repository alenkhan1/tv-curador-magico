# -*- coding: utf-8 -*-
import urllib.request
import re
import ssl
from adaptadores.modelos import HEADERS_WEB

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

url = "https://www.futbolenvivocolombia.com/deporte"
req = urllib.request.Request(url, headers=HEADERS_WEB)
with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
    html = resp.read().decode("utf-8", errors="ignore")

filas = re.findall(r"<tr[^>]*>.*?</tr>", html, flags=re.S)
for i, f in enumerate(filas):
    if "Pasto" in f or "Cúcuta" in f or "Bucaramanga" in f or "Llaneros" in f:
        print(f"=== MATCH ROW {i} ===")
        print(f)
        break
