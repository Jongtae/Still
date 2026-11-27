"""Candidate inspection and Work continuation through the real service, with fake wires."""
import json
from unittest import mock

from personal_agent import work_mode
from personal_agent.agent_runtime import Capabilities, ToolError
from personal_agent.capability_search import QueryRefused
from personal_agent.messages import msg
from personal_agent.providers import ModelAdapter
from personal_agent.settings_orchestrator import SettingsError
from personal_agent.skills import SkillError, SkillLibrary, reference_skills, tree_digest
from test_agency_loop import Script, call, finish, judgments
from test_owner_settings_conversation import _Case
from test_skill_supply import ADDRESS, COMMIT, PINNED, UPSTREAM, FakeGitHub, tarball, upstream_files


class SkillDiscovery(_Case):
    def setUp(self):
        super().setUp()
        self.github = FakeGitHub({COMMIT: tarball(UPSTREAM, COMMIT, upstream_files())}, {'main': COMMIT})
        self.library = SkillLibrary(self.store, transport=self.github)
        self.service.skill_transport = self.github
        self.request = 'Prepare the requested project update.'
        self.job = self.store.job(self.store.enqueue(self.request, 'original'))

    def search(self, query=None):
        return self.service.search_capabilities(self.job['id'], {'source': 'skill', 'query': query})

    def assert_only_inspected(self):
        self.assertEqual(list((self.library.root / '.inspect').glob('*')), [])
        self.assertEqual(list(self.library.root.glob('*.json')), [])
        self.assertFalse(self.library.content.exists())

    def test_inspect_pinned_candidate_without_installing(self):
        candidate = self.library.inspect_candidate(ADDRESS.replace(COMMIT, 'main'))
        self.assertEqual(candidate['address'], ADDRESS)
        self.assertEqual(candidate['revision'], COMMIT)
        self.assertEqual(candidate['licence'], 'Apache-2.0 (LICENSE.txt)')
        self.assertEqual(candidate['licence_source'], 'skill')
        self.assertEqual(candidate['digest'], tree_digest(PINNED))
        self.assertEqual(candidate['header'], '')  # Upstream starts with a level-two heading.
        self.assertTrue(candidate['supported'])
        self.assertTrue(candidate['untrusted'])
        self.assertFalse(candidate['installed'])
        self.assert_only_inspected()
        self.library.install(ADDRESS)
        self.assertTrue(self.library.inspect_candidate(ADDRESS)['installed'])

    def test_unsupported_candidate_and_draft_use_install_refusal(self):
        files = {**upstream_files(), 'skills/internal-comms/scripts/run.py': 'print(1)'}
        self.github.archives[COMMIT] = tarball(UPSTREAM, COMMIT, files)
        candidate = self.library.inspect_candidate(ADDRESS)
        self.assertFalse(candidate['supported'])
        self.assertIn('scripts_or_hooks_required', candidate['status'])
        self.assertEqual(candidate['refused'], [msg('skills.requires_scripts')])
        self.assert_only_inspected()
        with self.assertRaises(SkillError) as install:
            self.library.install(ADDRESS)
        with self.assertRaises(SettingsError) as draft:
            self.settings.propose('owner', 'web', 'skills', 'add', ADDRESS)
        self.assertEqual(str(draft.exception), str(install.exception))
        self.assertFalse(self.settings._drafts())
        self.assert_only_inspected()

    def test_reference_list_is_local_and_the_name_resolves_from_data(self):
        published = reference_skills()
        self.assertTrue(published)
        expected = [{**row, 'installed': False, 'adoptable': row['pinned']} for row in published]
        self.assertEqual(self.library.inspect_candidate(None), expected)
        listed = self.search('')
        self.assertEqual(listed['results'], expected)
        self.assertEqual(listed['result_count'], len(expected))
        self.assertFalse(listed['contacted'])
        self.assertEqual(self.github.urls, [])

        reference = self.library.root.parent / 'skill_references.json'
        reference.write_text(json.dumps([{'name': 'internal-comms', 'description': 'An update.', 'address': ADDRESS}]))
        with mock.patch('personal_agent.skills.reference_skills', side_effect=lambda: reference_skills(reference)):
            candidate = self.library.inspect_candidate('  INTERNAL-COMMS  ')
            self.assertEqual(candidate['address'], ADDRESS)
            self.assertFalse(self.library.inspect_candidate()[0]['installed'])
            self.library.install(ADDRESS)
            self.assertTrue(self.library.inspect_candidate()[0]['installed'])

    def test_secret_query_is_refused_before_transport(self):
        secret = 'stored-private-value'
        self.store.secret('telegram_token', secret)
        for query in (ADDRESS.replace('internal-comms', secret), 'password:hunter2'):
            with self.subTest(query=query), self.assertRaises(QueryRefused) as caught:
                self.search(query)
            self.assertEqual(caught.exception.code, 'private_capability_query')
        self.assertEqual(self.github.urls, [])

    def test_work_mode_uses_skill_transport_guard_even_for_a_branch(self):
        work_mode.set_enabled(self.store, True, surface='settings')
        caps = Capabilities(self.store, None, {}, '', self.job['id'], lambda *args: None,
                            settings=self.service.settings_tools(self.job))
        self.assertFalse(self.library.enabled())
        self.assertIn('capability_search', caps.offered_tools())
        self.assertFalse(caps.execute('capability_search', {'source': 'skill'})['contacted'])
        branch = ADDRESS.replace(COMMIT, 'main')
        with self.assertRaises(QueryRefused) as caught:
            self.search(branch)
        self.assertEqual(caught.exception.code, 'work_mode')
        with self.assertRaises(ToolError) as caught:
            caps.execute('capability_search', {'source': 'skill', 'query': ADDRESS})
        self.assertEqual(caught.exception.code, 'work_mode')
        self.assertEqual(self.github.urls, [])

    def test_github_unavailable_has_no_raw_error_text(self):
        raw_error = 'private transport diagnostic'
        self.service.skill_transport = mock.Mock(side_effect=SkillError(raw_error, 'source_unavailable'))
        result = self.search(ADDRESS)
        self.service.skill_transport.assert_called_once()
        self.assertEqual(result['state'], 'unavailable')
        self.assertTrue(result['contacted'])
        self.assertEqual(result['results'], [])
        self.assertNotIn(raw_error, json.dumps(result))
        self.assert_only_inspected()

    def test_add_preview_shows_licence_full_commit_and_model_reason(self):
        reason = 'It offers a method for project updates.'
        draft = self.settings.propose('owner', 'web', 'skills', 'add', ADDRESS, reason=reason)
        row = self.settings._drafts()[draft['draft_id']]
        self.assertIn('Apache-2.0', draft['summary'])
        self.assertEqual(row['exact_values'], ADDRESS)
        shown = self.settings.confirmation_text([row])
        for text in (UPSTREAM, 'skills/internal-comms', COMMIT, 'Apache-2.0', reason):
            self.assertIn(text, shown)
        self.assert_only_inspected()

    def test_confirmation_resumes_original_work_and_loaded_method_changes_tool_choice(self):
        name = 'update-method'
        skill = f'{name}/{name}'
        address = ADDRESS.replace('internal-comms', name)
        marker = 'Consult the saved notes before drafting the update.'
        header = 'A method for drafting updates.'
        instructions = (f'---\nname: {name}\ndescription: Draft project updates.\nlicense: MIT\n---\n'
                        f'{header}\n\n## Method\n{marker}\n')
        self.github.archives[COMMIT] = tarball(UPSTREAM, COMMIT, {f'skills/{name}/SKILL.md': instructions})
        self.library.set_enabled(True)
        self.service.save_model({'provider': 'compatible', 'endpoint': 'https://example.test/v1',
                                 'model': 'test-model', 'api_key': 'k'})
        self.assertTrue(self.service.test_model()['ok'])

        def propose(body):
            [discovery] = [json.loads(m['content']) for m in body['messages'] if m['role'] == 'tool']
            self.assertTrue(discovery['contacted'])
            [candidate] = discovery['results']
            self.assertEqual(candidate['header'], header)
            self.assertNotIn(marker, json.dumps(candidate))
            return {'tool_calls': [call('draft', 'settings_change', category='skills', setting='add',
                                        value=candidate['address'], continue_work='yes',
                                        reason='This method fits the requested project update.')]}

        def choose_tool(body):
            has_method = any(marker in json.loads(message['content']).get('instructions', '')
                             for message in body['messages'] if message['role'] == 'tool')
            return {'tool_calls': [call('check', 'list_notes' if has_method else 'list_roots')]}

        def complete():
            return finish('done', 'check', summary='Project update: the checklist is ready and schedule approval is pending.')

        self.service.decision_judge = judgments(True)
        original = self.job['id']
        self.service.adapter = ModelAdapter(Script(
            {'tool_calls': [call('discover', 'capability_search', source='skill', query=address)]},
            propose, {'content': 'I need confirmation to add the method before preparing the update.'}))
        self.assertTrue(self.service.run_one())
        [row] = self.settings.pending_for_work(original)
        self.assert_only_inspected()
        self.assertTrue(any(source['operation'] == 'capability_search'
                            for source in self.store.outside_content_sources(original)))
        [lookup] = self.service.work_information_use(original)['sent_to']['lookups']
        self.assertEqual((lookup['by'], lookup['sources']), ('github', [address]))

        # The original Work is waiting for confirmation. Run the same request without the skill.
        baseline = self.store.enqueue(self.request, 'baseline')
        self.service.adapter = ModelAdapter(Script(choose_tool, complete()))
        self.assertTrue(self.service.run_one())
        self.assertTrue(any(e['tool'] == 'list_roots' and e['status'] == 'succeeded'
                            for e in self.store.task_events(baseline)))

        self.settings.confirm(self.service.settings_owner(self.job), self.job['channel'], row['id'], row['digest'])
        self.assertEqual(self.store.job(original)['status'], 'queued')
        entry = self.service.skill_binding().entries[skill]

        def load_from_catalogue(body):
            self.assertTrue(any(skill in m['content'] for m in body['messages'] if m['role'] == 'system'))
            return {'tool_calls': [call('load', 'skill_load', skill=skill)]}

        self.service.adapter = ModelAdapter(Script(load_from_catalogue, choose_tool, complete()))
        self.assertTrue(self.service.run_one())
        result = self.store.job(original)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['message'], self.request)
        self.assertIn('schedule approval is pending', result['response'])
        self.assertEqual(len(self.store.jobs()), 2, 'only the original Work and baseline exist')
        events = [e for e in self.store.task_events(original) if e['status'] == 'succeeded']
        self.assertIn('list_notes', {e['tool'] for e in events})
        self.assertNotIn('list_roots', {e['tool'] for e in events})
        [loaded] = [e['trace']['evidence'] for e in events if e['tool'] == 'skill_load']
        for key, value in {'skill': skill, 'digest': entry['digest'], 'revision': COMMIT,
                           'licence': 'MIT', 'source': entry['source']}.items():
            self.assertEqual(loaded[key], value)
