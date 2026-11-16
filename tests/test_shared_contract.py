from __future__ import annotations
import contextlib
import os
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from runner.ledger import ArtifactLedger, ArtifactLedgerError, LEDGER_RELATIVE_PATH
from runner.shared_contract import load_contract, project_ledger, main

FIXTURE = Path(__file__).parent / 'fixtures/shared_contract'

class SharedContractTests(unittest.TestCase):
    contract_path = FIXTURE / "synthetic_contract.py"
    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract(cls.contract_path)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(FIXTURE, self.root, dirs_exist_ok=True)
        target = self.root / LEDGER_RELATIVE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / 'artifact_ledger.jsonl', target)
        self.ledger = ArtifactLedger(self.root)

    def rewrite(self, change):
        records = self.ledger.read_records()
        change(records)
        self.ledger.ledger_path.unlink()
        for r in records:
            if 'payload' in r:
                r.update(self.ledger.logical_record(record_type=r['record_type'], run_id=r['run_id'], recorded_at=r['recorded_at'],payload=r['payload']))
        self.ledger.append_records(records)

    def test_saved_six_records_coverage_alias_and_immutable_source(self):
        before = self.ledger.ledger_path.read_bytes()
        rows = project_ledger(self.root, self.contract)
        self.assertEqual(len(rows), 6)
        self.assertTrue(set(self.contract.REQUIRED_DRY_RUN_ARTIFACT_TYPES) <= {r['artifact_type'] for r in rows})
        for source, row in zip(self.ledger.read_records(), rows):
            self.assertEqual(json.loads(row['source_record_json']), source)
            self.assertEqual(row['artifact_path'], source.get('artifact_path'))
        self.assertEqual(rows[-1]['raw_lifecycle_state'], 'needs_human_approval')
        self.assertEqual(rows[-1]['lifecycle_state'], 'needs_review')
        rows[-1]['lifecycle_state'] = 'approved'
        self.assertEqual(project_ledger(self.root,self.contract)[-1]['lifecycle_state'],'needs_review')
        self.assertEqual(self.ledger.ledger_path.read_bytes(), before)

    def test_all_shared_aliases(self):
        for alias, expected in self.contract.LIFECYCLE_ALIASES.items():
            with self.subTest(alias=alias):
                self.rewrite(lambda rs: rs[-1]['payload'].update(to_status=alias))
                row = project_ledger(self.root,self.contract)[-1]
                self.assertEqual(row['raw_lifecycle_state'],alias)
                self.assertEqual(row['lifecycle_state'],expected)

    def test_invalid_missing_and_mission_states_fail_closed(self):
        for state in [None, '', 'bogus', 'completed', 'awaiting_review', 'plan_ready', ' approved']:
            with self.subTest(state=state):
                self.rewrite(lambda rs: rs[-1]['payload'].update(to_status=state))
                with self.assertRaises(ValueError): project_ledger(self.root,self.contract)
        self.rewrite(lambda rs: rs[-1]['payload'].pop('to_status'))
        with self.assertRaises(ValueError): project_ledger(self.root,self.contract)

    def test_missing_artifact_state(self):
        self.rewrite(lambda rs: rs[1]['metadata'].pop('status'))
        with self.assertRaises(ValueError): project_ledger(self.root,self.contract)

    def test_unknown_type(self):
        self.rewrite(lambda rs: rs[-1].update(record_type='Unknown'))
        with self.assertRaisesRegex(ArtifactLedgerError,'unknown artifact'): project_ledger(self.root,self.contract)

    def test_chain_tamper_precedes_projection(self):
        path = self.ledger.ledger_path
        path.write_bytes(path.read_bytes().replace(b'needs_human_approval',b'owner_approved',1))
        with self.assertRaisesRegex(ArtifactLedgerError,'hash'): project_ledger(self.root,self.contract)

    def test_artifact_tamper_and_missing_file(self):
        path = self.root / 'review_packet.md'
        path.write_text('tampered',encoding='utf-8')
        with self.assertRaisesRegex(ArtifactLedgerError,'integrity'): project_ledger(self.root,self.contract)
        path.unlink()
        with self.assertRaises((OSError,ArtifactLedgerError)): project_ledger(self.root,self.contract)

    def test_plan_ready_is_metadata_only(self):
        self.rewrite(lambda rs: rs[1].update(record_type='ExecutionPlan',metadata={'status':'plan_ready'}))
        row = project_ledger(self.root,self.contract)[1]
        self.assertIsNone(row['lifecycle_state'])
        self.assertEqual(json.loads(row['source_record_json'])['metadata']['status'],'plan_ready')

    def test_cli_parity_and_error(self):
        args = ['--project-root',str(self.root),'--contract',str(self.contract_path)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(args),0)
        self.assertEqual(json.loads(out.getvalue()),list(project_ledger(self.root,self.contract)))
        self.rewrite(lambda rs: rs[-1]['payload'].update(to_status='completed'))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(args),2)
        self.assertIn('unsupported lifecycle',err.getvalue())

    def test_actual_runner_saved_artifacts_project(self):
        from runner.harness import GovernedSkillRunner
        shutil.copytree(Path(__file__).parents[1] / 'schemas', self.root / 'schemas')
        skill = self.root / 'sample_skill.md'
        shutil.copyfile(Path(__file__).parent / 'fixtures/runner/sample_skill.md', skill)
        self.ledger.ledger_path.unlink()
        result = GovernedSkillRunner(self.root).run(skill)
        rows = project_ledger(self.root, self.contract)
        self.assertEqual(len(rows),6)
        self.assertEqual(rows[-1]['lifecycle_state'],'needs_review')
        self.assertEqual(rows[1]['artifact_path'],f'runs/{result.run_id}/run_state.json')

    def test_file_backed_records_require_identity(self):
        original = self.ledger.read_records()
        for kind in ('RunState', 'MemoryCheckpoint', 'EvalResult', 'ReviewPacket'):
            for value in ('missing', None):
                with self.subTest(kind=kind, value=value):
                    records = json.loads(json.dumps(original))
                    record = next(r for r in records if r['record_type'] == kind)
                    if value == 'missing':
                        record.pop('artifact_path')
                        record.pop('artifact_sha256')
                    else:
                        record['artifact_path'] = None
                        record['artifact_sha256'] = None
                    self.ledger.ledger_path.unlink()
                    self.ledger.append_records(records)
                    with self.assertRaisesRegex(ArtifactLedgerError, 'artifact identity'):
                        project_ledger(self.root, self.contract)

    def test_ledger_file_cannot_escape_through_symlink(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'ledger.jsonl'
            shutil.copyfile(self.ledger.ledger_path, target)
            self.ledger.ledger_path.unlink()
            self.ledger.ledger_path.symlink_to(target)
            with self.assertRaisesRegex(ArtifactLedgerError, 'escapes project root'):
                project_ledger(self.root, self.contract)

    def test_ledger_parent_cannot_escape_through_symlink(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'ledger'
            shutil.move(str(self.ledger.ledger_path.parent), target)
            self.ledger.ledger_path.parent.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ArtifactLedgerError, 'escapes project root'):
                project_ledger(self.root, self.contract)

    def test_absent_local_ledger_remains_empty(self):
        self.ledger.ledger_path.unlink()
        self.assertEqual(project_ledger(self.root, self.contract), ())

    def test_missing_and_invalid_contract_selection(self):
        broken = self.root / 'broken.py'
        broken.write_text('def invalid syntax', encoding='utf-8')
        for selected in (self.root / 'absent.py', self.root, self.root / 'review_packet.md', broken):
            with self.subTest(path=selected), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(['--project-root', str(self.root), '--contract', str(selected)]), 2)
                self.assertEqual(out.getvalue(), '')

    def test_entire_chain_validated_before_projection(self):
        records = self.ledger.read_records()
        records[-1]['prev_record_hash'] = 'broken'
        self.ledger.ledger_path.write_text('\n'.join(json.dumps(r) for r in records)+'\n',encoding='utf-8')
        with self.assertRaisesRegex(ArtifactLedgerError, 'chain'):
            project_ledger(self.root,self.contract)


class ExternalContractIntegrationTests(SharedContractTests):
    """Repeat behavior checks against an explicitly supplied trusted real contract."""

    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("OSINT_FORGE_TEST_CONTRACT")
        if not selected:
            raise unittest.SkipTest("real-contract integration NOT RUN: set OSINT_FORGE_TEST_CONTRACT to trusted Python contract")
        cls.contract_path = Path(selected)
        super().setUpClass()  # An explicitly selected missing/broken contract fails.
