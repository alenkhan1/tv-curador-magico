# -*- coding: utf-8 -*-
import urllib.request
import re
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

headers = {"User-Agent": "Mozilla/5.0"}
url = "https://www.futbolenvivoargentina.com/deporte"
req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
    html = resp.read().decode("utf-8", errors="ignore")

filas = re.findall(r"<tr[^>]*>.*?</tr>", html, flags=re.S)
for f in filas[1:3]:
    print("=== ROW FULL ===")
    print(f)
