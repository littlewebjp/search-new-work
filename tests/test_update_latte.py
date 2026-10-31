from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sync_latte import SyncError, WorkspaceSync
from update_latte import LatteUpdate

AT = '2026-10-31T12:00:00+09:00'


def fact(key='known', value='Synthetic known preference'):
    return {'key': key, 'label': 'Synthetic fact', 'value': value,
            'status': 'reported', 'source': 'Synthetic conversation'}


def proposal(record_id='p1'):
    return {'id': record_id, 'kind': 'approach', 'title': 'Synthetic proposal',
            'status': 'deferred', 'summary': 'Synthetic only', 'recommendation': '',
            'next_action': '', 'reasons': [], 'unknowns': [], 'evidence': []}


def payload():
    return {'profile': {'facts': [fact()], 'preferences': {}},
            'coverage': {'label': 'Tests', 'description': 'Synthetic records only',
                         'last_recorded_at': AT}, 'proposals': [proposal()]}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'private'
        self.dashboard = self.root / 'dashboard'
        self.dashboard.mkdir()
        for name in ['index.html', 'app.js', 'styles.css']:
            (self.dashboard/name).write_text('Generic test asset')
        self.memory = WorkspaceSync(self.data, dashboard_dir=self.dashboard)
        self.memory.record(payload())
        self.page = WorkspaceSync(self.data/'page-sync', dashboard_dir=self.dashboard)
        self.page.record(payload())
        self.updater = LatteUpdate(self.data, dashboard_dir=self.dashboard)
        self.change = deepcopy(self.page.read()['proposals'][0])
        self.change['summary'] = 'Updated synthetic reason'

    def tearDown(self):
        self.temp.cleanup()

    def grant(self):
        self.page.consent()

    def publish_stub(self):
        current = self.page.read()
        metadata = {'last_published_revision': current['revision'], 'commit': 'a'*40, 'at': AT}
        self.page._write(self.page.data_dir/'publication.json', metadata)
        return metadata

    def test_memory_only_never_copies_or_publishes_private_record(self):
        before = self.page.workspace_path.read_bytes()
        with patch.object(WorkspaceSync, 'publish', side_effect=AssertionError('must not publish')):
            result = self.updater.apply({'memory': {'profile': {'facts': [fact(value='PRIVATE-MEMORY-ONLY')]}}}, publish=True)
        self.assertTrue(result['memory_changed'])
        self.assertEqual(result['publish_status'], 'skipped_no_page_changes')
        self.assertEqual(self.page.workspace_path.read_bytes(), before)
        self.assertNotIn('PRIVATE-MEMORY-ONLY', before.decode())
        self.assertEqual(self.memory.read()['profile']['facts'][0]['value'], 'PRIVATE-MEMORY-ONLY')

    def test_invalid_page_or_missing_consent_preflight_preserves_both_stores(self):
        memory_before = self.memory.workspace_path.read_bytes()
        page_before = self.page.workspace_path.read_bytes()
        change = {'memory': {'profile': {'facts': [fact(value='different memory')]}},
                  'page': {'proposals': [self.change]}}
        with self.assertRaisesRegex(SyncError, 'consent'):
            self.updater.apply(change, publish=True)
        self.assertEqual(memory_before, self.memory.workspace_path.read_bytes())
        self.assertEqual(page_before, self.page.workspace_path.read_bytes())
        self.grant()
        change['page']['proposals'][0]['evidence'] = [{'conversation_id': 'missing', 'who': 'user', 'quote': 'invented'}]
        with self.assertRaises(SyncError):
            self.updater.apply(change, publish=True)
        self.assertEqual(memory_before, self.memory.workspace_path.read_bytes())
        self.assertEqual(page_before, self.page.workspace_path.read_bytes())

    def test_unchanged_updates_skip_crypto_and_git_after_confirmed_publish(self):
        self.grant()
        change = {'page': {'proposals': [self.change]}}
        with patch.object(WorkspaceSync, 'publish', side_effect=lambda **kw: self.publish_stub()) as publish:
            first = self.updater.apply(change, publish=True)
            self.assertEqual(first['publish_status'], 'published')
            self.assertEqual(publish.call_count, 1)
        before = self.page.workspace_path.read_bytes()
        with patch.object(WorkspaceSync, 'publish', side_effect=AssertionError('network not needed')), \
             patch.object(WorkspaceSync, '_build', side_effect=AssertionError('encryption not needed')):
            second = self.updater.apply(change, publish=True)
        self.assertEqual(second['publish_status'], 'unchanged')
        self.assertFalse(second['page_changed'])
        self.assertEqual(before, self.page.workspace_path.read_bytes())

    def test_dashboard_change_invalidates_cache_without_changing_page_revision(self):
        self.grant()
        change = {'page': {}}
        with patch.object(WorkspaceSync, 'publish', side_effect=lambda **kw: self.publish_stub()) as publish:
            self.updater.apply(change, publish=True)
            revision = self.page.read()['revision']
            (self.dashboard/'styles.css').write_text('Updated generic styles')
            result = self.updater.apply(change, publish=True)
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(result['publish_status'], 'published')
        self.assertEqual(self.page.read()['revision'], revision)

    def test_pending_update_can_retry_after_failed_push(self):
        self.grant()
        change = {'page': {'proposals': [self.change]}}
        with patch.object(WorkspaceSync, 'publish', side_effect=SyncError('Synthetic push failed')):
            with self.assertRaises(SyncError):
                self.updater.apply(change, publish=True)
        revision = self.page.read()['revision']
        self.assertEqual(self.page.read()['proposals'][0]['summary'], self.change['summary'])
        self.assertFalse((self.page.data_dir/'update-publication-cache.json').exists())
        with patch.object(WorkspaceSync, 'publish', side_effect=lambda **kw: self.publish_stub()):
            result = self.updater.apply(change, publish=True)
        self.assertFalse(result['page_changed'])
        self.assertEqual(result['revision'], revision)
        self.assertEqual(result['publish_status'], 'published')

    def test_no_publish_flag_keeps_valid_update_pending(self):
        with patch.object(WorkspaceSync, 'publish', side_effect=AssertionError('must not publish')):
            result = self.updater.apply({'page': {'proposals': [self.change]}})
        self.assertTrue(result['page_changed'])
        self.assertEqual(result['publish_status'], 'pending')
        self.assertFalse((self.page.data_dir/'publication-consent.json').exists())

    def test_minimal_row_edit_preserves_unmentioned_fields_and_is_idempotent(self):
        existing = self.page.read()['proposals'][0]
        existing['status'] = 'awaiting_decision'
        existing['dashboard'] = {'location': 'Synthetic city', 'description': 'Synthetic work',
                                 'status': 'Old status', 'detail_url': 'https://example.invalid/job'}
        existing['job'] = {'source_url': 'https://example.invalid/job', 'location': 'Synthetic city'}
        existing['user_decision'] = {'choice': 'yes', 'conversation_id': 'c1', 'quote': 'Synthetic reply'}
        existing['evidence'] = [{'conversation_id': 'c1', 'who': 'user', 'quote': 'Synthetic source quote'}]
        conversation = {'id': 'c1', 'at': AT, 'title': 'Synthetic source', 'kind': 'exchange',
                        'user_text': 'Synthetic source quote. Synthetic reply. Revised synthetic reply.',
                        'assistant_text': 'A synthetic response.', 'tags': [], 'source': 'Synthetic test'}
        self.page.record({'conversations': [conversation], 'proposals': [existing]})
        minimal = {'page': {'proposals': [{'id': 'p1', 'status': 'deferred',
                                           'dashboard': {'status': 'Need more details'},
                                           'job': {'location': 'New synthetic city'},
                                           'user_decision': {'quote': 'Revised synthetic reply'}}]}}
        self.assertTrue(self.updater.apply(minimal)['page_changed'])
        updated = self.page.read()['proposals'][0]
        self.assertEqual(updated['title'], existing['title'])
        self.assertEqual(updated['evidence'], existing['evidence'])
        self.assertEqual(updated['dashboard']['location'], 'Synthetic city')
        self.assertEqual(updated['dashboard']['detail_url'], 'https://example.invalid/job')
        self.assertEqual(updated['dashboard']['description'], 'Synthetic work')
        self.assertEqual(updated['dashboard']['status'], 'Need more details')
        self.assertEqual(updated['job']['source_url'], 'https://example.invalid/job')
        self.assertEqual(updated['job']['location'], 'New synthetic city')
        self.assertEqual(updated['user_decision']['choice'], 'yes')
        before = self.page.workspace_path.read_bytes()
        result = self.updater.apply(minimal)
        self.assertFalse(result['page_changed'])
        self.assertEqual(before, self.page.workspace_path.read_bytes())

    def test_invalid_minimal_patch_preflight_preserves_both_stores(self):
        memory_before = self.memory.workspace_path.read_bytes()
        page_before = self.page.workspace_path.read_bytes()
        change = {'memory': {'proposals': [{'id': 'p1', 'summary': 'Should not be saved'}]},
                  'page': {'proposals': [{'id': 'p1', 'status': 'applied'}]}}
        with self.assertRaises(SyncError):
            self.updater.apply(change)
        self.assertEqual(memory_before, self.memory.workspace_path.read_bytes())
        self.assertEqual(page_before, self.page.workspace_path.read_bytes())
        with self.assertRaises(SyncError):
            self.updater.apply({'page': {'proposals': [{'id': 'new', 'status': 'deferred'}]}}, allow_new_page_proposals=True)
        self.assertEqual(page_before, self.page.workspace_path.read_bytes())

    def test_memory_partial_rows_merge_and_explicit_arrays_replace(self):
        original = self.memory.read()['proposals'][0]
        original['reasons'] = ['Original reason']
        self.memory.record({'proposals': [original]})
        edit = {'memory': {'proposals': [{'id': 'p1', 'summary': 'Short edited summary', 'reasons': []}]}}
        self.assertTrue(self.updater.apply(edit)['memory_changed'])
        updated = self.memory.read()['proposals'][0]
        self.assertEqual(updated['summary'], 'Short edited summary')
        self.assertEqual(updated['title'], original['title'])
        self.assertEqual(updated['status'], original['status'])
        self.assertEqual(updated['reasons'], [])
        self.assertFalse(self.updater.apply(edit)['memory_changed'])

    def test_partial_employment_preference_preserves_other_conditions_and_facts(self):
        preferences = {'employment': 'employee', 'remote_days': 2,
                       'min_monthly_salary': 250000, 'location': 'Synthetic city'}
        self.memory.record({'profile': {'preferences': preferences}})
        facts_before = deepcopy(self.memory.read()['profile']['facts'])
        page_before = self.page.workspace_path.read_bytes()
        edit = {'memory': {'profile': {'preferences': {'employment': 'contract'}}}}
        result = self.updater.apply(edit)
        self.assertTrue(result['memory_changed'])
        profile = self.memory.read()['profile']
        self.assertEqual(profile['preferences'], {**preferences, 'employment': 'contract'})
        self.assertEqual(profile['facts'], facts_before)
        self.assertEqual(self.page.workspace_path.read_bytes(), page_before)
        before = self.memory.workspace_path.read_bytes()
        self.assertFalse(self.updater.apply(edit)['memory_changed'])
        self.assertEqual(self.memory.workspace_path.read_bytes(), before)

    def test_scope_rejects_full_brain_assets_new_facts_and_unrequested_proposals(self):
        for page in [ {'assets': []}, {'documents': []}, {'profile': {'facts': [fact('new-private-fact')]}},
                      {'profile': {'preferences': {'new-private-preference': 'value'}}},
                      {'proposals': [proposal('unrequested')]}, {'profile': {'secret': 'no'}} ]:
            with self.subTest(keys=list(page)):
                before = self.page.workspace_path.read_bytes()
                with self.assertRaises(SyncError):
                    self.updater.apply({'page': page})
                self.assertEqual(self.page.workspace_path.read_bytes(), before)
        result = self.updater.apply({'page': {'proposals': [proposal('new-approved')]}}, allow_new_page_proposals=True)
        self.assertTrue(result['page_changed'])
        self.assertEqual(len(self.page.read()['proposals']), 2)

    def test_missing_page_key_is_not_initialized_and_legacy_files_remain_untouched(self):
        legacy = self.data/'latte.db'; legacy.write_bytes(b'Synthetic legacy file')
        legacy_before = legacy.read_bytes()
        (self.page.data_dir/'sync-key.bin').unlink()
        with self.assertRaises(SyncError):
            self.updater.apply({'page': {}})
        self.assertFalse((self.page.data_dir/'sync-key.bin').exists())
        self.assertEqual(legacy.read_bytes(), legacy_before)

    def test_real_publication_is_limited_and_preserves_working_index(self):
        bare, repo = self.root/'remote.git', self.root/'repo'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(bare)], check=True)
        subprocess.run(['git', 'init', '--quiet', str(repo)], check=True)
        def git(*args):
            return subprocess.check_output(['git', '-c', 'user.name=Test', '-c', 'user.email=test@localhost', *args], cwd=repo, text=True).strip()
        (repo/'retained.txt').write_text('Generic public file')
        git('add', 'retained.txt'); git('commit', '-m', 'Synthetic initial')
        git('remote', 'add', 'origin', str(bare)); git('push', 'origin', 'HEAD:refs/heads/latte-site')
        index_before = (repo/'.git/index').read_bytes()
        self.grant()
        updater = LatteUpdate(self.data, repo_dir=repo, dashboard_dir=self.dashboard)
        result = updater.apply({'page': {'proposals': [self.change]}}, publish=True)
        self.assertEqual(result['publish_status'], 'published')
        self.assertEqual(index_before, (repo/'.git/index').read_bytes())
        files = git('ls-tree', '-r', '--name-only', result['commit']).splitlines()
        self.assertIn('retained.txt', files)
        self.assertIn('workspace.enc.json', files)
        self.assertNotIn('sync-key.bin', files)
        self.assertNotIn('workspace.json', files)
        self.assertEqual(git('status', '--porcelain'), '')


if __name__ == '__main__':
    unittest.main()
