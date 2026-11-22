import os,pathlib,subprocess,sys
root=pathlib.Path.cwd(); scratch=root/'.factory-p021'; worker=pathlib.Path(r'C:\Users\zacha\Workspace\_artifacts\2026-11-16_ai_factory_portfolio\projects\p021\worker')
env=os.environ.copy(); env.update(PYTHONDONTWRITEBYTECODE='1',TEMP=str(scratch/'tmp'),TMP=str(scratch/'tmp'),PYTHONPATH=str(root))
cmd=[sys.executable,'-B',str(scratch/'replay.py')]
log=worker/'05-replay.log'
with log.open('x',encoding='utf-8') as f:
 f.write(f'cwd={root}\ncommand={subprocess.list2cmdline(cmd)}\nTEMP={env["TEMP"]}\nTMP={env["TMP"]}\nPYTHONPATH={env["PYTHONPATH"]}\nPYTHONDONTWRITEBYTECODE=1\n'); f.flush()
 p=subprocess.run(cmd,cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT)
 f.write(f'exit_code={p.returncode}\n')
print(log.read_text(encoding='utf-8'))
sys.exit(p.returncode)
