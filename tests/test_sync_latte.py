import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sync_latte import AAD, ASSET_AAD, MAX_ASSET_BYTES, MAX_WORKSPACE_BYTES, SyncError, WorkspaceSync

AT = '2026-10-31T09:00:00+09:00'


def sample():
    return {
        'coverage': {'label': 'Synthetic conversation', 'description': 'Tests only', 'last_recorded_at': AT},
        'conversations': [{'id': 'c1', 'at': AT, 'title': 'Synthetic title', 'kind': 'exchange',
                           'user_text': 'I prefer illustration work. SYNTHETIC-PRIVATE-MARKER-382749',
                           'assistant_text': 'Let us compare the actual work.', 'summary': '', 'tags': ['test'],
                           'source': 'Synthetic test conversation'}],
        'proposals': [{'id': 'p1', 'kind': 'approach', 'title': 'Compare roles', 'status': 'awaiting_decision',
                       'summary': 'A test proposal', 'recommendation': 'Read the work description',
                       'reasons': ['A reported preference'], 'unknowns': ['Actual tasks'],
                       'next_action': 'Discuss in the current chat',
                       'evidence': [{'conversation_id': 'c1', 'who': 'user', 'quote': 'I prefer illustration work.'}]}],
        'decisions': [{'id': 'd1', 'at': AT, 'title': 'A recorded choice', 'choice': 'Compare first',
                       'reason': 'Synthetic test', 'conversation_ids': ['c1'], 'proposal_ids': ['p1']}],
        'documents': [{'id': 'doc1', 'title': 'Preparation note', 'status': 'draft', 'body': 'Synthetic note.',
                       'conversation_ids': ['c1'], 'proposal_id': 'p1'}],
        'activities': [{'id': 'a1', 'at': AT, 'kind': 'recorded', 'text': 'Recorded a synthetic conversation',
                        'conversation_id': 'c1', 'proposal_id': 'p1'}],
    }


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        dashboard = self.root / 'dashboard'
        dashboard.mkdir()
        for name in ('index.html', 'styles.css', 'app.js'):
            (dashboard / name).write_text('Generic dashboard asset')
        self.sync = WorkspaceSync(self.root / 'private', dashboard_dir=dashboard)

    def tearDown(self):
        self.folder.cleanup()

    def test_key_is_private_stable_and_never_silently_replaced(self):
        key = (self.sync.data_dir / 'sync-key.bin').read_bytes()
        self.assertEqual(len(key), 32)
        self.assertEqual(stat.S_IMODE(self.sync.data_dir.stat().st_mode), 0o700)
        for name in ('sync-key.bin', 'workspace.json', 'access-url.txt'):
            self.assertEqual(stat.S_IMODE((self.sync.data_dir / name).stat().st_mode), 0o600)
        WorkspaceSync(self.sync.data_dir, dashboard_dir=self.sync.dashboard_dir)
        self.assertEqual((self.sync.data_dir / 'sync-key.bin').read_bytes(), key)
        (self.sync.data_dir / 'sync-key.bin').write_bytes(b'bad')
        with self.assertRaises(SyncError):
            WorkspaceSync(self.sync.data_dir)
        self.assertEqual((self.sync.data_dir / 'sync-key.bin').read_bytes(), b'bad')

    def test_record_is_idempotent_preserves_ids_and_rejects_bad_references(self):
        first = self.sync.record(sample())
        self.assertEqual(first['revision'], 1)
        self.assertEqual(first['coverage']['record_count'], 1)
        self.assertEqual(self.sync.record(sample()), first)
        extra = {'conversations': [{'id': 'c2', 'at': AT, 'title': 'A summary', 'kind': 'summary',
                                   'summary': 'An explicitly summarized earlier exchange.', 'source': 'Synthetic summary'}]}
        second = self.sync.record(extra)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(len(second['conversations']), 2)
        bad = deepcopy(sample())
        bad['proposals'][0]['evidence'][0]['conversation_id'] = 'missing'
        with self.assertRaises(SyncError):
            self.sync.record(bad)
        bad = deepcopy(sample())
        bad['proposals'][0]['evidence'][0]['quote'] = 'An invented quotation'
        with self.assertRaises(SyncError):
            self.sync.record(bad)
        self.assertEqual(self.sync.read(), second)

    def test_missing_key_with_existing_records_or_publication_requires_restore(self):
        self.sync.record(sample())
        workspace_before = self.sync.workspace_path.read_bytes()
        url_before = (self.sync.data_dir / 'access-url.txt').read_bytes()
        (self.sync.data_dir / 'sync-key.bin').unlink()
        with self.assertRaisesRegex(SyncError, 'Restore sync-key.bin'):
            WorkspaceSync(self.sync.data_dir, dashboard_dir=self.sync.dashboard_dir)
        self.assertFalse((self.sync.data_dir / 'sync-key.bin').exists())
        self.assertEqual(self.sync.workspace_path.read_bytes(), workspace_before)
        self.assertEqual((self.sync.data_dir / 'access-url.txt').read_bytes(), url_before)
        publication_only = self.root / 'publication-only'
        publication_only.mkdir()
        (publication_only / 'publication.json').write_text('{"last_published_revision": 1}')
        with self.assertRaisesRegex(SyncError, 'Restore sync-key.bin'):
            WorkspaceSync(publication_only, dashboard_dir=self.sync.dashboard_dir)
        self.assertFalse((publication_only / 'sync-key.bin').exists())
        self.assertFalse((publication_only / 'workspace.json').exists())

    def test_application_states_require_actual_job_and_submission_evidence(self):
        payload = sample()
        proposal = payload['proposals'][0]
        proposal['status'] = 'waiting_to_apply'
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        proposal['kind'] = 'job'
        proposal['job'] = {'source_url': 'https://example.invalid/synthetic-only', 'verified_at': AT}
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        proposal['packet_ready'] = True
        for invalid in ('javascript:alert(1)', 'https://user:pass@example.invalid/job', 'https://example.invalid:bad/job', 'https://example.invalid/has space'):
            proposal['job']['source_url'] = invalid
            with self.assertRaises(SyncError):
                self.sync.record(payload)
        proposal['job']['source_url'] = 'https://example.invalid/synthetic-only'
        self.sync.record(payload)
        proposal['status'] = 'applied'
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        proposal['submission_confirmed_at'] = AT
        self.assertEqual(self.sync.record(payload)['proposals'][0]['status'], 'applied')

    def test_queued_application_records_yes_before_packet_is_ready(self):
        payload = sample()
        proposal = payload['proposals'][0]
        proposal.update(kind='job', status='application_queued', display_group='applied',
                        job={'source_url': 'https://example.invalid/test-job', 'verified_at': AT},
                        dashboard={'location': 'Synthetic city', 'description': 'Illustration work', 'status': '応募待ち', 'detail_url': 'https://example.invalid/test-job'})
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        proposal['user_decision'] = {'choice': 'yes', 'conversation_id': 'c1', 'quote': 'An invented yes'}
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        payload['conversations'][0]['user_text'] += '\n○ This one, please prepare.'
        proposal['user_decision']['quote'] = '○ This one, please prepare.'
        result = self.sync.record(payload)
        self.assertFalse(result['proposals'][0].get('packet_ready', False))
        self.assertEqual(result['proposals'][0]['status'], 'application_queued')
        proposal['status'] = 'applied'
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        self.assertEqual(self.sync.read(), result)

    def test_minimal_dashboard_fields_and_groups_cannot_fake_submission(self):
        payload = sample()
        row = payload['proposals'][0]
        row.update(status='new_proposal', display_group='new', dashboard={'location': 'Unconfirmed', 'description': 'A potential direction', 'status': '新しい提案', 'detail_url': ''})
        self.assertEqual(self.sync.record(payload)['proposals'][0]['display_group'], 'new')
        for group in ('applied', 'deferred', 'unknown', [], 1):
            row['display_group'] = group
            with self.assertRaises(SyncError):
                self.sync.record(payload)
        row['display_group'] = None
        self.assertIsNone(self.sync.record(payload)['proposals'][0]['display_group'])
        row['dashboard']['detail_url'] = 'https://user:password@example.invalid/job'
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        row['dashboard']['detail_url'] = 'javascript:alert(1)'
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        row['dashboard']['detail_url'] = ''
        row['dashboard']['status'] = '応募中'
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        row['dashboard']['status'] = '新しい提案'
        row['dashboard']['extra'] = 'Not an allowed visible field'
        with self.assertRaises(SyncError):
            self.sync.record(payload)

    def test_legacy_workspace_without_assets_is_not_rewritten_on_read(self):
        self.sync.record(sample())
        old = self.sync.read()
        old.pop('assets')
        self.sync.workspace_path.write_text(json.dumps(old))
        before = self.sync.workspace_path.read_bytes()
        again = WorkspaceSync(self.sync.data_dir, dashboard_dir=self.sync.dashboard_dir)
        self.assertEqual(again.read()['assets'], [])
        self.assertEqual(again.read()['revision'], old['revision'])
        self.assertEqual(again.workspace_path.read_bytes(), before)

    def test_private_file_archive_and_verified_export_are_immutable(self):
        self.sync.record(sample())
        content = b'%PDF-FAKE\x00\xffSYNTHETIC-BINARY-PORTFOLIO-382749'
        source = self.root/'synthetic-portfolio.pdf'
        source.write_bytes(content)
        asset = self.sync.archive_file(source, 'Synthetic portfolio', 'c1')
        raw = self.sync.data_dir/'vault'/(asset['id']+'.bin')
        self.assertEqual(asset['filename'], source.name)
        self.assertEqual(asset['sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(asset['size'], len(content))
        self.assertEqual(asset['conversation_id'], 'c1')
        self.assertEqual(raw.read_bytes(), content)
        self.assertEqual(stat.S_IMODE(raw.stat().st_mode), 0o600)
        source.write_bytes(b'User later edited the original')
        export = self.root/'exported.pdf'
        self.assertEqual(self.sync.extract_asset(asset['id'], export), export)
        self.assertEqual(export.read_bytes(), content)
        self.assertEqual(stat.S_IMODE(export.stat().st_mode), 0o600)
        with self.assertRaises(SyncError):
            self.sync.extract_asset(asset['id'], export)
        self.assertEqual(export.read_bytes(), content)
        raw.write_bytes(b'corrupted')
        with self.assertRaises(SyncError):
            self.sync.extract_asset(asset['id'], self.root/'must-not-exist.pdf')
        self.assertFalse((self.root/'must-not-exist.pdf').exists())
        with self.assertRaises(SyncError):
            self.sync.build()

    def test_archive_rejects_symlinks_oversize_bad_reference_and_conflicts(self):
        self.sync.record(sample())
        source = self.root/'test.bin'
        source.write_bytes(b'abc')
        link = self.root/'link.bin'
        link.symlink_to(source)
        with self.assertRaises(SyncError):
            self.sync.archive_file(link, 'Symlink')
        with self.assertRaises(SyncError):
            self.sync.archive_file(self.root, 'Directory')
        with patch('sync_latte.MAX_ASSET_BYTES', 2):
            with self.assertRaises(SyncError):
                self.sync.archive_file(source, 'Too large')
        with self.assertRaises(SyncError):
            self.sync.archive_file(source, 'Missing conversation', 'not-present')
        before = self.sync.read()
        asset = self.sync.archive_file(source, 'Original file')
        from types import SimpleNamespace
        with patch('sync_latte.uuid4', return_value=SimpleNamespace(hex=asset['id'])):
            with self.assertRaises(SyncError):
                self.sync.archive_file(source, 'Conflict')
        self.assertEqual(len(self.sync.read()['assets']), 1)
        self.assertEqual((self.sync.data_dir/'vault'/(asset['id']+'.bin')).read_bytes(), b'abc')
        self.assertEqual(self.sync.read()['revision'], before['revision']+1)
        bad = deepcopy(asset)
        bad['id'] = '../escape'
        with self.assertRaises(SyncError):
            self.sync.record({'assets':[bad]})
        bad = deepcopy(asset)
        bad['size'] = MAX_ASSET_BYTES+1
        with self.assertRaises(SyncError):
            self.sync.record({'assets':[bad]})

    def test_archive_build_encrypts_binary_and_keeps_filename_in_encrypted_metadata(self):
        self.sync.record(sample())
        source = self.root/'PRIVATE-FILENAME-ONLY-IN-ENCRYPTED-METADATA.pdf'
        content = b'%PDF-FAKE\x00\xffSYNTHETIC-BINARY-PRIVATE-CONTENT-382749'
        source.write_bytes(content)
        asset = self.sync.archive_file(source, 'Synthetic archive', 'c1')
        output = self.sync.build()
        envelope = json.loads((output/'vault'/(asset['id']+'.enc.json')).read_text())
        self.assertEqual(envelope['format'], 'latte-asset-v1')
        key = (self.sync.data_dir/'sync-key.bin').read_bytes()
        nonce = base64.b64decode(envelope['nonce'])
        encrypted = base64.b64decode(envelope['ciphertext'])
        plain = AESGCM(key).decrypt(nonce, encrypted, ASSET_AAD+asset['id'].encode())
        self.assertEqual(plain, content)
        with self.assertRaises(InvalidTag):
            AESGCM(key).decrypt(nonce, encrypted, ASSET_AAD+b'wrong-asset-id')
        all_files = b''.join(p.read_bytes() for p in output.rglob('*') if p.is_file())
        self.assertNotIn(content, all_files)
        self.assertNotIn(source.name.encode(), all_files)
        self.assertNotIn(key, all_files)
        self.sync.build()
        self.assertNotEqual(json.loads((output/'vault'/(asset['id']+'.enc.json')).read_text())['nonce'], envelope['nonce'])

    def test_historical_dates_do_not_require_invented_clock_times(self):
        payload = sample()
        for collection in ('conversations', 'decisions', 'activities'):
            payload[collection][0]['at'] = '2026-10-31'
        result = self.sync.record(payload)
        self.assertEqual(result['conversations'][0]['at'], '2026-10-31')
        self.assertEqual(self.sync.record(payload), result)
        for invalid in ('2026-02-30', '2026-1-1', 'not-a-date', '2026-10-31T09:00:00'):
            payload['conversations'][0]['at'] = invalid
            with self.assertRaises(SyncError):
                self.sync.record(payload)
        payload = sample()
        payload['coverage']['last_recorded_at'] = '2026-10-31'
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        self.assertEqual(self.sync.read(), result)

    def test_oversized_and_non_json_records_preserve_previous_state(self):
        before = self.sync.record(sample())
        payload = sample()
        payload['documents'][0]['body'] = 'x' * MAX_WORKSPACE_BYTES
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        self.assertEqual(self.sync.read(), before)
        payload = {'profile': {'preferences': {'salary': float('nan')}}}
        with self.assertRaises(SyncError):
            self.sync.record(payload)
        self.assertEqual(self.sync.read(), before)

    def test_ciphertext_roundtrip_tamper_wrong_key_and_random_nonce(self):
        expected = self.sync.record(sample())
        output = self.sync.build()
        encrypted = json.loads((output / 'workspace.enc.json').read_text())
        key = (self.sync.data_dir / 'sync-key.bin').read_bytes()
        nonce = base64.b64decode(encrypted['nonce'])
        ciphertext = base64.b64decode(encrypted['ciphertext'])
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, AAD)
        self.assertEqual(json.loads(plaintext), expected)
        self.assertEqual(len(nonce), 12)
        with self.assertRaises(InvalidTag):
            AESGCM(os.urandom(32)).decrypt(nonce, ciphertext, AAD)
        with self.assertRaises(InvalidTag):
            AESGCM(key).decrypt(nonce, ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]), AAD)
        self.sync.build()
        again = json.loads((output / 'workspace.enc.json').read_text())
        self.assertNotEqual(again['nonce'], encrypted['nonce'])
        bundle = b''.join(p.read_bytes() for p in output.iterdir())
        self.assertNotIn(b'SYNTHETIC-PRIVATE-MARKER-382749', bundle)
        self.assertNotIn(key, bundle)
        self.assertNotIn(base64.urlsafe_b64encode(key).rstrip(b'='), bundle)
        self.assertEqual(sorted(p.name for p in output.iterdir()), ['.nojekyll', 'app.js', 'index.html', 'styles.css', 'workspace.enc.json'])

    def test_publish_requires_both_explicit_consent_and_command_flag(self):
        with patch.object(self.sync, '_git') as git:
            with self.assertRaises(SyncError):
                self.sync.publish(True)
            git.assert_not_called()
            self.sync.consent()
            with self.assertRaises(SyncError):
                self.sync.publish(False)
            git.assert_not_called()
        self.assertIsNone(self.sync.status()['last_published_revision'])


class GitPublicationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.remote = self.root / 'remote.git'
        self.repo = self.root / 'repo'
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        subprocess.run(['git', 'init', str(self.repo)], check=True, capture_output=True)
        self.git('config', 'user.name', 'Synthetic Test')
        self.git('config', 'user.email', 'test@localhost')
        (self.repo / 'retained.txt').write_text('An unrelated public file')
        self.git('add', 'retained.txt')
        self.git('commit', '-m', 'Synthetic initial public files')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', 'origin', 'HEAD:refs/heads/latte-site')
        (self.repo / 'local-only.txt').write_text('Uncommitted working tree file')
        self.git('add', 'local-only.txt')
        self.before_index = (self.repo / '.git/index').read_bytes()
        dashboard = self.root / 'dashboard'
        dashboard.mkdir()
        for name in ('index.html', 'styles.css', 'app.js'):
            (dashboard / name).write_text('Generic synthetic asset')
        self.sync = WorkspaceSync(self.root / 'private', repo_dir=self.repo, dashboard_dir=dashboard)
        self.sync.record(sample())
        self.sync.consent()

    def git(self, *args, input=None):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True, input=input, stderr=subprocess.DEVNULL).strip()

    def tearDown(self):
        self.folder.cleanup()

    def test_publish_retains_unrelated_files_without_touching_worktree_or_index(self):
        result = self.sync.publish(True)
        self.assertEqual(result['last_published_revision'], 1)
        self.assertEqual((self.repo / '.git/index').read_bytes(), self.before_index)
        self.assertEqual((self.repo / 'local-only.txt').read_text(), 'Uncommitted working tree file')
        files = self.git('ls-tree', '-r', '--name-only', result['commit']).splitlines()
        self.assertIn('retained.txt', files)
        self.assertIn('workspace.enc.json', files)
        self.assertNotIn('local-only.txt', files)
        self.assertNotIn('workspace.json', files)
        remote = self.git('ls-remote', 'origin', 'refs/heads/latte-site').split()[0]
        self.assertEqual(remote, result['commit'])
        self.assertFalse(self.sync.status()['deployment_verified'])

    def test_publication_includes_only_encrypted_nested_assets(self):
        source = self.root/'synthetic-private-filename.pdf'
        content = b'%PDF-SYNTHETIC-NESTED-PRIVATE-BINARY\x00\xff'
        source.write_bytes(content)
        asset = self.sync.archive_file(source, 'Synthetic archive', 'c1')
        result = self.sync.publish(True)
        files = self.git('ls-tree', '-r', '--name-only', result['commit']).splitlines()
        encrypted_path = 'vault/'+asset['id']+'.enc.json'
        self.assertIn(encrypted_path, files)
        self.assertNotIn(source.name, files)
        self.assertFalse(any(path.endswith('.bin') for path in files))
        envelope = json.loads(self.git('show',result['commit']+':'+encrypted_path))
        key = (self.sync.data_dir/'sync-key.bin').read_bytes()
        plain = AESGCM(key).decrypt(base64.b64decode(envelope['nonce']),base64.b64decode(envelope['ciphertext']),ASSET_AAD+asset['id'].encode())
        self.assertEqual(plain, content)
        self.assertEqual((self.repo/'.git/index').read_bytes(),self.before_index)
        self.assertEqual(self.sync.status()['asset_count'],1)

    def test_concurrent_remote_update_rejects_push_and_preserves_local_record(self):
        original_git = self.sync._git
        parent = self.git('rev-parse', 'HEAD')
        tree = self.git('rev-parse', 'HEAD^{tree}')
        other = self.git('commit-tree', tree, '-p', parent, input='Concurrent synthetic publication\n')
        def racing_git(*args, **kwargs):
            if args[0] == 'push':
                self.git('push', 'origin', other + ':refs/heads/latte-site')
            return original_git(*args, **kwargs)
        with patch.object(self.sync, '_git', side_effect=racing_git):
            with self.assertRaises(SyncError):
                self.sync.publish(True)
        self.assertIsNone(self.sync.status()['last_published_revision'])
        self.assertEqual(self.sync.read()['revision'], 1)
        self.assertEqual((self.repo / '.git/index').read_bytes(), self.before_index)
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/latte-site').split()[0], other)


if __name__ == '__main__':
    unittest.main()
