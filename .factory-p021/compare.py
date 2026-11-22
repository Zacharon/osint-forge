import json, pathlib
w=pathlib.Path(r'C:\Users\zacha\Workspace\_artifacts\2026-11-16_ai_factory_portfolio\projects\p021\worker')
a=json.loads((w/'before-inventory.json').read_text(encoding='utf-8')); b=json.loads((w/'after-inventory.json').read_text(encoding='utf-8'))
for side in ('candidate','canonical'):
 for category in ('tracked_nonsecret_sha256','runtime_root_metadata'):
  x,y=a[side][category],b[side][category]
  changed=[k for k in sorted(set(x)|set(y)) if x.get(k)!=y.get(k)]
  print(side,category,'changed=',changed)
for name in ('02-governance.log','03-ingestion.log','04-replay.log'):
 raw=(w/name).read_bytes(); (w/(name+'.readable.txt')).write_text(raw.replace(b'\x00',b'').decode('utf-8',errors='replace'),encoding='utf-8')
