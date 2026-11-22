import asyncio, hashlib, json, os, pathlib, socket, sys, tempfile
root = pathlib.Path.cwd().resolve()
scratch = root / '.factory-p021'
tempfile.tempdir = str(scratch / 'tmp')
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
attempts = []
def deny(*args, **kwargs):
    attempts.append('network operation denied')
    raise RuntimeError('network disabled in synthetic replay')
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.socket.sendto = deny
socket.create_connection = deny
socket.getaddrinfo = deny
from osint_forge.governance.sat_preconditions import ReportPreconditionError
from osint_forge.orchestrator.mission_supervisor import MissionSupervisor, MissionStatus
from osint_forge.review.human_review import ReviewRequest
mission = 'synthetic-p021-review'
ws = scratch / 'workspace'
db = scratch / 'synthetic.db'
fixture = root / 'tests' / 'fixtures' / 'gdelt'
def make(): return MissionSupervisor(mission, 'Analyze synthetic public event records', workspace_root=ws, db_path=db, gdelt_fixture_dir=fixture)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def blocked(label, f, phrase):
    try: f()
    except ReportPreconditionError as e:
        assert phrase in str(e), str(e)
        print(label, type(e).__name__, str(e))
    else: raise AssertionError(label + ' did not block')
s = make()
try:
    result = loop.run_until_complete(s.run_dry_run())
    print('dry_run', result['status'])
    assert result['status'] == 'awaiting_review'
    report = ws / 'output/final-report.md'
    blocked('before_review', s.generate_report, 'not approved')
    assert not report.exists()
    for item in s.context.pending_reviews:
        req = ReviewRequest(mission, item['artifact_type'], item['artifact_path'], item['artifact_hash'])
        assert s.review_gate.approve(req, reviewer='synthetic-test-reviewer', reason='synthetic test approval')
    reviews = [dict(r) for r in s.store.conn.execute('SELECT artifact_path,artifact_hash,status,reviewed_by FROM review_requests WHERE mission_id=? ORDER BY artifact_path',(mission,))]
    print('reviews', json.dumps(reviews, sort_keys=True))
    assert len(reviews)==2 and all(r['status']=='APPROVED' for r in reviews)
    print('approved_preconditions', json.dumps(s.validate_report_preconditions(), sort_keys=True))
    ach = ws / 'output/sat/ach-evidence-matrix.md'
    original=ach.read_bytes()
    (scratch / 'original-ach.bin').write_bytes(original)
    print('original_sha256', sha(ach))
    ach.write_bytes(original+b'\nSynthetic correction after approval.\n')
    print('changed_sha256',sha(ach))
    blocked('changed_report', s.generate_report, 'artifact hash mismatch')
    print('final_report_exists_after_rejection', report.exists())
    assert not report.exists()
    s.store.close()
    s=make()
    blocked('changed_resume', s.resume, 'artifact file hash mismatch')
    print('resume_status',s.context.status.value)
    assert s.context.status == MissionStatus.RESUME_BLOCKED_HASH_MISMATCH
    s.store.close()
    ach.write_bytes(original)
    assert ach.read_bytes()==(scratch/'original-ach.bin').read_bytes()
    print('restored_sha256', sha(ach))
    s=make()
    print('recovered_resume',s.resume()['status'])
    print('recovered_preconditions',json.dumps(s.validate_report_preconditions(),sort_keys=True))
    print('recovered_report',json.dumps(s.generate_report(),sort_keys=True))
    assert report.exists() and s.context.status == MissionStatus.COMPLETED
    counts={t:s.store.count_rows(t,mission) for t in ('artifacts','governance_events','ingested_records','review_requests','tool_runs','tool_outputs','tool_executions')}
    print('row_counts',json.dumps(counts,sort_keys=True))
    print('network_attempts',len(attempts))
    assert not attempts
finally:
    s.store.close()
    loop.close()
