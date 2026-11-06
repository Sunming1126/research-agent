import time,json
from urllib.request import urlopen
for _ in range(30):
    try:
        with urlopen('http://127.0.0.1:8080/health',timeout=1) as r: assert r.status==200; break
    except OSError: time.sleep(1)
else: raise SystemExit('health unavailable')
rows=[]
for i in range(10):
    with urlopen('http://127.0.0.1:8080/health',timeout=2) as r:
        assert r.status==200
        rows.append(json.loads(r.read(4096)))
print(json.dumps({'count':len(rows),'responses':rows}))
