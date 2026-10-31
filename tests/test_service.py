import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import memory as M
import mcp_server as MCP
import semantic as S
import service


class Encoder:
    calls = 0

    def embed(self, texts, query=False):
        self.calls += 1
        return [[1.0, 0.0] if ('Shanghai' in text or '上海' in text or '居住' in text) else [0.0, 1.0] for text in texts]


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = pathlib.Path(self.temporary.name) / 'memory.json'
        self.encoder = Encoder()

    def call(self, name, **args):
        return service.execute(self.path, name, args)

    def remember(self, text, **args):
        return self.call('memory_remember', layer=args.pop('layer', 'preferences'), text=text, **args)['item']

    def test_empty_store_auto_created(self):
        self.assertEqual(self.call('memory_stats')['archive'], 0)

    def test_uuid_ids_and_same_content_dedup(self):
        store = M.empty_store()
        ids = {M.make_id('tasks', 'same-prefix-' + str(i)) for i in range(1000)}
        self.assertEqual(len(ids), 1000)
        first, _ = M.add_item(store, 'profile', text='Unique fixture')
        second, created = M.add_item(store, 'profile', text='Unique fixture')
        self.assertFalse(created)
        self.assertEqual(first['id'], second['id'])

    def test_purge_sanitizes_backup_cache_and_temp(self):
        item = self.remember('private synthetic lunar garden')
        stale = pathlib.Path(str(self.path) + '.777.tmp')
        stale.write_text(item['text'])
        cache = pathlib.Path(str(self.path) + '.vectors.sqlite')
        cache.write_text('synthetic derived embedding')
        self.call('memory_forget', id=item['id'], purge=True)
        for path in [self.path, pathlib.Path(str(self.path) + '.bak')]:
            self.assertNotIn(item['text'], path.read_text())
        self.assertFalse(stale.exists())
        self.assertFalse(cache.exists())

    def test_purge_archive_and_prevents_reimport(self):
        item = self.remember('private synthetic archived observation')
        self.call('memory_forget', id=item['id'])
        self.assertEqual(self.call('memory_forget', id=item['id'], purge=True)['removed'], 1)
        self.assertEqual(M.load(self.path)['archive'], [])
        with self.assertRaises(M.ValidationError):
            self.remember(item['text'])

    def test_purge_removes_derived_digest(self):
        store = M.empty_store()
        store['layers']['fragments']['maxItems'] = 2
        first, _ = M.add_item(store, 'fragments', text='Synthetic first observation should be removed')
        for i in range(4):
            M.add_item(store, 'fragments', text='Synthetic observation number ' + str(i))
        M.save(self.path, store)
        self.call('memory_forget', id=first['id'], purge=True)
        self.assertNotIn(first['text'], self.path.read_text())
        self.assertNotIn(first['text'], pathlib.Path(str(self.path) + '.bak').read_text())

    def test_mcp_prune_compacts_and_archives(self):
        store = M.empty_store()
        store['layers']['fragments']['maxItems'] = 2
        store['layers']['fragments']['items'] = [{'id': str(i), 'text': 'Synthetic observation ' + str(i), 'at': M.now_stamp()} for i in range(4)]
        M.save(self.path, store)
        self.call('memory_prune', force=True)
        data = M.load(self.path)
        self.assertTrue(any(row.get('digest') for row in data['layers']['fragments']['items']))
        self.assertEqual(len(data['archive']), 3)

    def test_prune_cleans_expired_archive_without_new_entries(self):
        store = M.empty_store()
        store['archive'] = [{'layer': 'profile', 'prunedAt': '2000-01-01', 'item': {'id': 'old', 'text': 'Old entry'}}]
        M.save(self.path, store)
        self.call('memory_prune', force=True)
        self.assertEqual(M.load(self.path)['archive'], [])

    def test_dry_run_does_not_modify_store(self):
        self.remember('Ordinary fixture')
        before = self.path.read_bytes()
        self.call('memory_prune', dryRun=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_thread_lock_does_not_reenter_from_other_thread(self):
        entered = threading.Event()
        def contender():
            with M.store_lock(self.path, timeout=1):
                entered.set()
        with M.store_lock(self.path):
            thread = threading.Thread(target=contender)
            thread.start()
            self.assertFalse(entered.wait(0.1))
        thread.join(2)
        self.assertTrue(entered.is_set())

    def test_independent_process_writes_are_not_lost(self):
        M.save(self.path, M.empty_store())
        program = ('import sys;sys.path.insert(0,sys.argv[1]);import memory as M;'
                   '[(M.mutate(sys.argv[2],lambda s,i=i:M.add_item(s,"preferences",'
                   'text="worker-%s-entry-%d"%(sys.argv[3],i)))) for i in range(10)]')
        children = [subprocess.Popen([sys.executable, '-X', 'utf8', '-c', program, str(ROOT), str(self.path), str(i)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(4)]
        try:
            for child in children:
                _, stderr = child.communicate(timeout=30)
                self.assertEqual(child.returncode, 0, stderr.decode('utf-8', errors='replace'))
            self.assertEqual(len(M.load(self.path)['layers']['preferences']['items']), 40)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=10)

    def test_secret_rejected_before_storage(self):
        with self.assertRaises(M.ValidationError):
            self.remember('api_key=SYNTHETICKEY01234567890')
        self.assertNotIn('SYNTHETICKEY', self.path.read_text())

    def test_short_delete_query_rejected(self):
        self.remember('使用中文回复')
        with self.assertRaises(M.ValidationError):
            self.call('memory_forget', query='用')
        self.assertEqual(self.call('memory_stats')['layers']['preferences']['items'], 1)

    def test_input_validation(self):
        for arguments in [{'layer': 'rules'}, {'layer': 'rules', 'text': 'x', 'ttl': -1},
                          {'layer': 'rules', 'text': 'x', 'unknown': 1}, {'layer': 'rules', 'text': 'x', 'confidence': float('nan')}]:
            with self.assertRaises((M.ValidationError, ValueError)):
                service.execute(self.path, 'memory_remember', arguments)

    def test_task_id_status_result(self):
        self.remember('Synthetic task', layer='tasks', id='task-1', status='doing')
        item = self.remember('Synthetic task finished', layer='tasks', id='task-1', status='done', result='Verified')
        self.assertEqual((item['id'], item['status'], item['result']), ('task-1', 'done', 'Verified'))
        self.assertEqual(len(M.load(self.path)['layers']['tasks']['items']), 1)

    def test_task_custom_ttl_survives_recall_and_update(self):
        item = self.remember('Persistent fixture task', layer='tasks', ttl=0, id='persistent')
        self.call('memory_recall', query='Persistent', mode='lexical')
        self.remember('Persistent fixture task updated', layer='tasks', id=item['id'], status='done')
        stored = M.load(self.path)['layers']['tasks']['items'][0]
        self.assertEqual(stored['ttlDays'], 0)
        self.assertNotIn('expiresAt', stored)

    def test_fragment_recall(self):
        self.remember('Observation about a lunar garden', layer='fragments')
        result = self.call('memory_recall', layer='fragments', query='lunar', mode='lexical')
        self.assertEqual(len(result['items']), 1)

    def test_scope_isolation(self):
        item = self.remember('Tenant A private observation', scope='A')
        self.assertEqual(self.call('memory_recall', scope='B', query='observation', mode='lexical')['items'], [])
        self.assertEqual(self.call('memory_forget', scope='B', id=item['id'])['removed'], 0)
        with self.assertRaises(M.ValidationError):
            self.remember('Overwrite from B', scope='B', id=item['id'])

    def test_fragment_digests_do_not_mix_scopes(self):
        store = M.empty_store()
        store['layers']['fragments']['maxItems'] = 2
        for scope in ['A', 'B']:
            for i in range(4):
                M.add_item(store, 'fragments', text=scope + ' private observation ' + str(i), scope=scope)
        for scope in ['A', 'B']:
            text = ' '.join(row['text'] for row in M.live_items(store, 'fragments', scope))
            self.assertNotIn(('B' if scope == 'A' else 'A') + ' private', text)

    def test_user_correction_supersedes_inferred_fact(self):
        old = self.remember('我住在北京', layer='profile', source='user', confirmed=True)
        new = self.remember('我搬到上海', layer='profile', source='user', confirmed=True)
        store = M.load(self.path)
        self.assertEqual([row['id'] for row in store['layers']['profile']['items']], [new['id']])
        self.assertEqual(store['archive'][-1]['item']['id'], old['id'])

    def test_agent_cannot_overwrite_confirmed_fact(self):
        old = self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        proposed = self.remember('我住在北京', layer='profile', source='agent', confidence=0.99)
        self.assertIn('conflictId', proposed)
        self.assertEqual(M.load(self.path)['layers']['profile']['items'][0]['id'], old['id'])

    def test_user_confirmation_closes_pending_contradiction(self):
        self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        self.remember('我住在北京', layer='profile')
        self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        self.assertFalse(any(item['status'] == 'pending' for item in M.load(self.path)['conflicts']))

    def test_id_update_does_not_inherit_user_confirmation(self):
        old = self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        proposed = self.remember('我住在北京', layer='profile', id=old['id'])
        self.assertIn('conflictId', proposed)
        self.assertEqual(M.load(self.path)['layers']['profile']['items'][0]['fact']['value'], '上海')

    def test_same_fact_paraphrase_dedup_and_trust_preserved(self):
        old = self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        duplicate = self.remember('我的常住城市是上海', layer='profile')
        self.assertEqual(old['id'], duplicate['id'])
        self.assertTrue(duplicate['confirmed'])
        self.assertEqual(duplicate['source'], 'user')

    def test_purge_conflict_and_resolution_history(self):
        self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        proposed = self.remember('我住在北京', layer='profile')
        self.call('memory_conflicts', id=proposed['conflictId'], action='accept_new', confirmed=True)
        current = M.load(self.path)['layers']['profile']['items'][0]
        self.call('memory_forget', id=current['id'], purge=True)
        self.assertNotIn('北京', self.path.read_text(encoding='utf-8'))
        self.assertNotIn('北京', pathlib.Path(str(self.path) + '.bak').read_text(encoding='utf-8'))

    def test_legacy_duplicate_ids_repaired_once(self):
        store = M.empty_store()
        store['layers']['preferences']['items'] = [{'id': 'collision', 'text': 'First'}, {'id': 'collision', 'text': 'Second'}]
        M.save(self.path, store)
        first = M.load(self.path)
        identities = [row['id'] for row in first['layers']['preferences']['items']]
        self.assertEqual(len(set(identities)), 2)
        self.assertEqual(identities, [row['id'] for row in M.load(self.path)['layers']['preferences']['items']])

    def test_purge_failure_does_not_commit_main_store(self):
        item = self.remember('Synthetic private observation')
        cache = pathlib.Path(str(self.path) + '.vectors.sqlite')
        cache.write_text('derived fixture')
        original = pathlib.Path.unlink
        def blocked(target, *args, **kwargs):
            if target == cache:
                raise PermissionError('simulated locked index')
            return original(target, *args, **kwargs)
        with mock.patch.object(pathlib.Path, 'unlink', blocked):
            with self.assertRaises(M.MemoryStoreError):
                self.call('memory_forget', id=item['id'], purge=True)
        self.assertIn(item['text'], self.path.read_text())
        self.call('memory_forget', id=item['id'], purge=True)
        self.assertNotIn(item['text'], self.path.read_text())

    def test_purge_retry_on_same_object_remains_privacy_safe(self):
        item = self.remember('Synthetic privacy retry observation')
        state = M.load(self.path)
        M.forget_items(state, item_id=item['id'], purge=True)
        cache = pathlib.Path(str(self.path) + '.vectors.sqlite')
        cache.write_text('derived fixture')
        unlink = pathlib.Path.unlink
        def blocked(target, *args, **kwargs):
            if target == cache:
                raise PermissionError('fixture index is locked')
            return unlink(target, *args, **kwargs)
        with mock.patch.object(pathlib.Path, 'unlink', blocked):
            with self.assertRaises(M.MemoryStoreError):
                M.save(self.path, state)
        self.assertTrue(state['_purge_pending'])
        M.save(self.path, state)
        self.assertNotIn(item['text'], pathlib.Path(str(self.path) + '.bak').read_text())
        self.assertNotIn('_purge_pending', self.path.read_text())

    def test_same_lock_object_can_nest_three_times(self):
        lock = M.store_lock(self.path)
        with lock:
            with lock:
                with lock:
                    self.assertIn(lock.depth_key, M._LOCK_DEPTH)
            self.assertIn(lock.depth_key, M._LOCK_DEPTH)
        self.assertNotIn(lock.depth_key, M._LOCK_DEPTH)

    def test_explicit_conflict_resolution(self):
        self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        proposed = self.remember('我住在北京', layer='profile')
        with self.assertRaises(M.ValidationError):
            self.call('memory_conflicts', id=proposed['conflictId'], action='accept_new')
        self.call('memory_conflicts', id=proposed['conflictId'], action='accept_new', confirmed=True)
        self.assertEqual(M.load(self.path)['layers']['profile']['items'][0]['fact']['value'], '北京')

    def test_observed_fact_can_correct_agent_guess(self):
        self.remember('My host database is sqlite', layer='profile', fact={'subject': 'host', 'predicate': 'database', 'value': 'sqlite'})
        item = self.remember('Observed PostgreSQL database', layer='profile', source='observation', confidence=0.99,
                             fact={'subject': 'host', 'predicate': 'database', 'value': 'postgresql'})
        self.assertIn('supersedes', item)

    def test_different_fact_subjects_do_not_conflict(self):
        self.remember('项目A的数据库是SQLite', layer='profile')
        self.remember('项目B的数据库是PostgreSQL', layer='profile')
        self.assertEqual(len(M.load(self.path)['layers']['profile']['items']), 2)

    def test_bilingual_correction_uses_same_fact_identity(self):
        self.remember('I live in Shanghai.', layer='profile', source='user', confirmed=True)
        corrected = self.remember('我住在北京', layer='profile', source='user', confirmed=True)
        self.assertIn('supersedes', corrected)
        self.assertEqual(len(M.load(self.path)['layers']['profile']['items']), 1)

    def test_legacy_plain_text_fact_can_be_corrected(self):
        store = M.empty_store()
        store['layers']['profile']['items'] = [{'id': 'legacy', 'text': '我住在北京'}]
        M.save(self.path, store)
        corrected = self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        self.assertIn('legacy', corrected['supersedes'])
        self.assertEqual(len(M.load(self.path)['layers']['profile']['items']), 1)

    def test_fact_correction_cannot_be_bypassed_by_changing_layer(self):
        old = self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        conflict = self.remember('我住在北京', layer='preferences', source='agent')
        self.assertIn('conflictId', conflict)
        corrected = self.remember('我住在北京', layer='preferences', source='user', confirmed=True)
        self.assertIn(old['id'], corrected['supersedes'])
        store = M.load(self.path)
        self.assertEqual(store['layers']['profile']['items'], [])
        self.assertEqual(store['archive'][-1]['layer'], 'profile')

    def test_observation_cannot_overwrite_rule_from_another_layer(self):
        self.remember('使用中文回复', layer='rules')
        conflict = self.remember('使用英文回复', layer='preferences', source='observation', confidence=0.99)
        self.assertIn('conflictId', conflict)
        self.assertEqual(len(M.load(self.path)['layers']['rules']['items']), 1)

    def test_negated_preference_corrects_atomic_fact(self):
        self.remember('我喜欢咖啡', source='user', confirmed=True)
        corrected = self.remember('我不再喜欢咖啡', source='user', confirmed=True)
        self.assertEqual(corrected['fact']['value'], 'false')
        self.assertIn('supersedes', corrected)

    def test_superseded_restore_does_not_revive_old_fact(self):
        old = self.remember('我住在北京', layer='profile', source='user', confirmed=True)
        self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        restored = self.call('memory_restore', id=old['id'])
        self.assertEqual(restored['restored'], [])

    def test_restore_does_not_cross_scope_deduplicate(self):
        old = self.remember('Same synthetic preference', scope='A')
        self.call('memory_forget', id=old['id'], scope='A')
        other = self.remember('Same synthetic preference', scope='B')
        result = self.call('memory_restore', id=old['id'], scope='A')
        self.assertEqual(len(result['restored']), 1)
        self.assertEqual(self.call('memory_stats', scope='A')['layers']['preferences']['items'], 1)
        self.assertEqual(self.call('memory_stats', scope='B')['layers']['preferences']['items'], 1)
        self.assertEqual(M.live_items(M.load(self.path), 'preferences', 'B')[0]['id'], other['id'])

    def test_restore_forgotten_fact_does_not_contradict_current_fact(self):
        old = self.remember('我住在北京', layer='profile', source='user', confirmed=True)
        self.call('memory_forget', id=old['id'])
        self.remember('我住在上海', layer='profile', source='user', confirmed=True)
        result = self.call('memory_restore', id=old['id'])
        self.assertEqual(result['restored'], [])
        self.assertIn('Conflicts with an active fact', result['skipped'][0][1])
        self.assertEqual(M.load(self.path)['layers']['profile']['items'][0]['fact']['value'], '上海')

    def test_cli_context_limit_and_summary_ttl_are_honored(self):
        for i in range(3):
            self.remember('Synthetic preference ' + str(i))
        child = subprocess.run([sys.executable, str(ROOT / 'memory.py'), '--store', str(self.path), 'context',
            '--limit', '1', '--mode', 'lexical', '--budget', '0'], capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.count('Synthetic preference'), 1)
        child = subprocess.run([sys.executable, str(ROOT / 'memory.py'), '--store', str(self.path), 'summary',
            '--text', 'Synthetic task summary.', '--layer', 'tasks', '--ttl', '3'],
            capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(M.load(self.path)['layers']['tasks']['items'][0]['ttlDays'], 3)

    def test_rules_not_silently_dropped(self):
        self.remember('删除任何文件前必须得到用户确认', layer='rules')
        with self.assertRaises(M.ContextBudgetError):
            self.call('memory_context', budget=1)
        result = self.call('memory_context', budget=300)
        self.assertIn('删除任何文件', result['text'])
        self.assertLessEqual(M.est_tokens(result['text']), 300)
        self.assertLessEqual(M.est_tokens(json.dumps(result, ensure_ascii=False)), 300)

    def test_context_includes_more_than_twenty_rules_or_errors(self):
        for i in range(25):
            self.remember('Explicit safety rule number ' + str(i), layer='rules')
        result = self.call('memory_context', budget=0)
        self.assertIn('Explicit safety rule number 24', result['text'])
        self.assertIn('Explicit safety rule number 0', result['text'])

    def test_scope_prune_does_not_return_or_remove_other_scope(self):
        self.remember('A private task', layer='tasks', scope='A')
        store = M.load(self.path)
        store['layers']['tasks']['items'][0]['expiresAt'] = '2000-01-01'
        M.save(self.path, store)
        report = self.call('memory_prune', scope='B', force=True)
        self.assertEqual(report['archived'], 0)
        self.assertEqual(len(M.load(self.path)['layers']['tasks']['items']), 1)

    def test_vector_search_without_lexical_overlap(self):
        self.remember('I live in Shanghai.', layer='profile')
        self.remember('A collection of songs', layer='profile')
        with mock.patch.object(S, 'provider', return_value=(self.encoder, 'fixture-model')):
            result = self.call('memory_recall', query='居住城市', mode='semantic')
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['text'], 'I live in Shanghai.')
        self.assertEqual(result['search']['used'], 'semantic')

    def test_vector_cache_reuses_document_embeddings(self):
        self.remember('I live in Shanghai.')
        with mock.patch.object(S, 'provider', return_value=(self.encoder, 'fixture-model')):
            self.call('memory_recall', query='居住城市', mode='semantic', touch=False)
            first = self.encoder.calls
            self.call('memory_recall', query='居住城市', mode='semantic', touch=False)
        self.assertEqual(self.encoder.calls - first, 1)

    def test_purged_vectors_not_reinserted_from_old_snapshot(self):
        item = self.remember('I live in Shanghai.')
        with mock.patch.object(S, 'provider', return_value=(self.encoder, 'fixture-model')):
            _, pending, _ = S.rank(self.path, '居住城市', [('preferences', item)], 'semantic')
        self.call('memory_forget', id=item['id'], purge=True)
        S.commit_pending(self.path, pending, [])
        self.assertFalse(pathlib.Path(str(self.path) + '.vectors.sqlite').exists())

    def test_missing_vectors_are_explicit_not_faked(self):
        self.remember('Searchable observation')
        with mock.patch.object(S, 'provider', side_effect=S.SemanticUnavailable('test: model missing')):
            result = self.call('memory_recall', query='Searchable', mode='hybrid')
            self.assertEqual(result['search']['used'], 'lexical')
            self.assertIn('missing', result['search']['reason'])
            with self.assertRaises(S.SemanticUnavailable):
                self.call('memory_recall', query='Searchable', mode='semantic')

    def test_hybrid_fallback_discards_partial_vector_scores(self):
        self.remember('Searchable Shanghai observation')
        self.remember('Searchable music observation')
        class BrokenDimensions:
            def embed(self, texts, query=False):
                return [[1, 0]] if query else [[1, 0], [1, 0, 0]]
        with mock.patch.object(S, 'provider', return_value=(BrokenDimensions(), 'broken-fixture')):
            result = self.call('memory_recall', query='Searchable', mode='hybrid')
        self.assertEqual(result['search']['used'], 'lexical')
        self.assertTrue(all(row['match']['semantic'] is None for row in result['items']))

    def test_only_returned_recall_entries_renew(self):
        self.remember('Searchable observation one')
        self.remember('Searchable observation two')
        self.call('memory_recall', query='Searchable', mode='lexical', limit=1)
        rows = M.load(self.path)['layers']['preferences']['items']
        self.assertEqual(sum(row.get('hits', 0) for row in rows), 1)

    def test_mcp_survives_invalid_argument_then_ping(self):
        old = MCP.STORE
        MCP.STORE = str(self.path)
        self.addCleanup(setattr, MCP, 'STORE', old)
        response = MCP.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                               'params': {'name': 'memory_remember', 'arguments': {'layer': 'rules'}}})
        self.assertTrue(response['result']['isError'])
        self.assertEqual(MCP.handle({'id': 2, 'method': 'ping'})['result'], {})

    def test_mcp_survives_corrupt_store_in_real_process(self):
        self.path.write_text('{broken', encoding='utf-8')
        requests = [{'id': 1, 'method': 'tools/call', 'params': {'name': 'memory_stats'}}, {'id': 2, 'method': 'ping'}]
        child = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'mcp_server.py')],
            env=dict(os.environ, MEMORY_STORE=str(self.path)), input='\n'.join(map(json.dumps, requests)) + '\n',
            capture_output=True, text=True, encoding='utf-8', timeout=10)
        replies = [json.loads(line) for line in child.stdout.splitlines()]
        self.assertEqual(child.returncode, 0)
        self.assertTrue(replies[0]['result']['isError'])
        self.assertEqual(replies[1]['result'], {})

    def test_mcp_utf8_independent_of_windows_console_encoding(self):
        message = {'id': 1, 'method': 'tools/call', 'params': {'name': 'memory_remember',
            'arguments': {'layer': 'profile', 'text': '我住在上海'}}}
        raw = json.dumps(message, ensure_ascii=False).encode('utf-8') + b'\n' + b'\xff\n' + b'{"id":2,"method":"ping"}\n'
        child = subprocess.run([sys.executable, str(ROOT / 'mcp_server.py')], input=raw,
            env=dict(os.environ, MEMORY_STORE=str(self.path), PYTHONIOENCODING='cp1252'), capture_output=True, timeout=10)
        replies = [json.loads(line) for line in child.stdout.decode('utf-8').splitlines()]
        self.assertEqual(child.returncode, 0)
        self.assertEqual(replies[0]['result']['structuredContent']['item']['text'], '我住在上海')
        self.assertEqual(replies[1]['error']['code'], -32700)
        self.assertEqual(replies[2]['result'], {})

    def test_unpaired_surrogate_is_rejected(self):
        with self.assertRaises(M.ValidationError):
            self.remember('invalid \ud800 text')

    def test_invalid_vector_dimensions_rejected(self):
        for vector in [[], [float('nan')], [0, 0]]:
            with self.assertRaises(S.SemanticUnavailable):
                S.unit(vector)

    def test_remote_embedding_contract_on_loopback(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'data': [{'index': i, 'embedding': [3, 4]} for i, _ in enumerate(request['input'])]}).encode())
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            encoder = S.RemoteEncoder('fixture-model', 'http://127.0.0.1:' + str(server.server_port) + '/v1')
            self.assertEqual(encoder.embed(['synthetic input']), [[0.6, 0.8]])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_embedding_url_cannot_forward_credentials(self):
        for url in ['http://example.com/v1', 'https://user:password@example.com/v1', 'https://example.com/v1?token=fixture']:
            with self.assertRaises(S.SemanticUnavailable):
                S.RemoteEncoder('fixture-model', url)

    def test_cli_and_mcp_rank_in_same_order(self):
        self.remember('Desktop documents')
        self.remember('Desktop documents with extra context')
        expected = self.call('memory_recall', query='Desktop documents', mode='lexical', touch=False)
        child = subprocess.run([sys.executable, str(ROOT / 'memory.py'), '--store', str(self.path), 'recall',
            'Desktop documents', '--mode', 'lexical', '--json', '--no-touch'], capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        actual = json.loads(child.stdout)['preferences']
        self.assertEqual([row['id'] for row in actual], [row['id'] for row in expected['items']])

    @unittest.skipUnless(os.environ.get('MEMORY_TEST_LOCAL_MODEL') == '1', 'Optional downloaded local model')
    def test_real_multilingual_vectors(self):
        self.remember('I live in Shanghai.', layer='profile')
        self.remember('The music collection contains classical piano recordings.', layer='profile')
        self.remember('My cat likes sleeping on the sofa.', layer='profile')
        result = self.call('memory_recall', query='我居住在哪个城市', mode='semantic', touch=False)
        self.assertEqual(result['items'][0]['text'], 'I live in Shanghai.')
        self.assertGreater(result['items'][0]['match']['semantic'], 0.45)


if __name__ == '__main__':
    unittest.main(verbosity=2)
