import hashlib, json, os, pathlib, subprocess, sys
candidate = pathlib.Path.cwd()
canonical = pathlib.Path(r'C:\Users\zacha\Workspace\20_Products\OSINT_Forge')
def inventory(root):
    names = subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
    hashes = {}
    for name in names:
        if not name or any(part.lower().startswith('.env') for part in pathlib.PurePosixPath(name).parts): continue
        p = root / name
        hashes[name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'MISSING'
    runtime = {}
    for name in ['osint_forge.db','osint_forge.db-wal','osint_forge.db-shm','workspace']:
        p = root / name
        if p.exists():
            st=p.stat(); runtime[name]={'type':'directory' if p.is_dir() else 'file','size':st.st_size,'mtime_ns':st.st_mtime_ns}
        else: runtime[name] = 'ABSENT'
    return {'tracked_nonsecret_sha256': hashes, 'runtime_root_metadata': runtime}
result={'candidate':inventory(candidate),'canonical':inventory(canonical),'scope':'Tracked non-.env files; metadata only for named default runtime roots; excludes runtime payloads and unrelated caches.'}
pathlib.Path(sys.argv[1]).write_text(json.dumps(result,indent=2,sort_keys=True),encoding='utf-8')
print('tracked counts',len(result['candidate']['tracked_nonsecret_sha256']),len(result['canonical']['tracked_nonsecret_sha256']))
