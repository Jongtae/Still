"""CONNECT-SLOT-01: generic slot authority boundaries; synthetic API only."""
import json

from personal_agent.api_requests import ApiError, ApiRequests, save_slot, slots
from personal_agent.agent_runtime import ToolError
from test_api_requests import Base, HOST, ACCOUNT, READ, ORDER


class MoneySlots(Base):
    def mark(self, **kwargs):
        return save_slot(self.store, 'fake-holdings', [HOST], self.key,
                         subject_field='$.account_id', subject_value=ACCOUNT, **kwargs)

    def test_every_non_read_method_on_money_slot_requires_payment_approval(self):
        self.assertTrue(self.mark(money=True)['money'])
        caps = self.caps()
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            for declared in ('read', 'navigate', 'mutate', 'payment'):
                with self.subTest(method=method, declared=declared):
                    with self.assertRaises(ToolError) as refused:
                        caps.execute('api_request', {**ORDER, 'method': method, 'effect': declared})
                    self.assertEqual(refused.exception.code, 'approval_required')
                    self.assertIn(', payment)', self.approvals.requested[-1][1])
        self.assertFalse(self.api.calls)

    def test_money_reads_stay_reads_and_approved_change_is_reported_as_payment(self):
        self.mark(money=True)
        caps = self.caps()
        self.assertEqual(caps.execute('api_request', READ)['provenance']['effect'], 'read')
        with self.assertRaises(ToolError):
            caps.execute('api_request', {**ORDER, 'effect': 'read'})
        self.approvals.approve_last()
        result = caps.execute('api_request', {**ORDER, 'effect': 'read'})
        self.assertEqual(result['provenance']['effect'], 'payment')
        self.assertEqual(len(self.api.orders), 1)
        with self.assertRaises(ToolError):
            caps.execute('api_request', {**ORDER, 'effect': 'read'})
        self.assertEqual(len(self.api.orders), 1)
        self.assertNotIn(self.key, json.dumps(self.store.task_events(self.job_id)))

    def test_approval_before_money_marking_does_not_cover_current_slot(self):
        caps = self.caps()
        with self.assertRaises(ToolError):
            caps.execute('api_request', ORDER)
        previous = self.approvals.requested[-1][0]
        self.approvals.approve_last()
        self.mark(money=True)
        with self.assertRaises(ToolError):
            caps.execute('api_request', ORDER)
        self.assertNotEqual(previous, self.approvals.requested[-1][0])
        self.assertIn(', payment)', self.approvals.requested[-1][1])
        self.assertFalse(self.api.calls)

    def test_omission_preserves_mark_and_registration_cannot_lower_it(self):
        self.mark(money=True)
        self.assertTrue(self.mark()['money'])
        before = slots(self.store)
        with self.assertRaises(ApiError):
            self.mark(money=False)
        self.assertEqual(slots(self.store), before)

    def test_invalid_mark_is_not_coerced_and_corrupt_slot_is_not_offered(self):
        before = slots(self.store)
        for value in ('false', 'true', 0, 1, [], {}):
            with self.subTest(value=value), self.assertRaises(ApiError):
                self.mark(money=value)
        self.assertEqual(slots(self.store), before)
        rows = self.store.config('api_slots')
        rows['fake-holdings']['money'] = 'false'
        self.store.put('api_slots', rows)
        self.assertNotIn('fake-holdings', slots(self.store))
        with self.assertRaises(ApiError):
            self.mark()

    def legacy_slot(self):
        rows = self.store.config('api_slots')
        rows['fake-holdings'].pop('money', None)
        self.store.put('api_slots', rows)

    def test_unclassified_legacy_slot_keeps_reads_and_requires_payment_approval(self):
        self.legacy_slot()
        self.assertTrue(slots(self.store)['fake-holdings']['money'])
        caps = self.caps()
        self.assertEqual(caps.execute('api_request', READ)['provenance']['effect'], 'read')
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            with self.subTest(method=method), self.assertRaises(ToolError) as refused:
                caps.execute('api_request', {**ORDER, 'method': method, 'effect': 'read'})
            self.assertEqual(refused.exception.code, 'approval_required')
            self.assertIn(', payment)', self.approvals.requested[-1][1])
        self.assertFalse(self.api.orders)
        with self.assertRaises(ToolError):
            caps.execute('api_request', ORDER)
        self.approvals.approve_last()
        self.assertEqual(caps.execute('api_request', ORDER)['provenance']['effect'], 'payment')
        self.assertEqual(len(self.api.orders), 1)
        with self.assertRaises(ToolError):
            caps.execute('api_request', ORDER)
        self.assertEqual(len(self.api.orders), 1)

    def test_legacy_resave_without_classification_preserves_payment_floor(self):
        self.legacy_slot()
        self.assertTrue(self.mark()['money'])
        self.assertIs(self.store.config('api_slots')['fake-holdings']['money'], True)

    def test_owner_can_explicitly_classify_unmarked_legacy_slot_as_nonpayment(self):
        self.legacy_slot()
        self.assertFalse(self.mark(money=False)['money'])
        caps = self.caps()
        result = caps.execute('api_request', ORDER)
        self.assertEqual(result['provenance']['effect'], 'mutate')
        self.assertEqual(self.approvals.requested, [])
        self.assertEqual(len(self.api.orders), 1)
        # This fixture acknowledges without a bound subject: never replay it.
        with self.assertRaises(ToolError) as repeated:
            caps.execute('api_request', ORDER)
        self.assertEqual(repeated.exception.code, 'effect_unknown')
        self.assertEqual(len(self.api.orders), 1)


class EncryptedSlots(Base):
    def setUp(self):
        super().setUp()
        from personal_agent.browser_jar import MemoryKey
        self.store.api_slot_key = MemoryKey()

    def save_encrypted(self, secret=None, **kwargs):
        return save_slot(self.store, 'fake-holdings', [HOST], secret or self.key,
                         subject_field='$.account_id', subject_value=ACCOUNT,
                         encrypted=True, **kwargs)

    def verifier(self, probe=None, current=lambda: None):
        api = ApiRequests(self.store, self.job_id, transport=self.api, clock=self.api.now, sleep=lambda _: None)
        return lambda candidate: api.check_connection(candidate, probe or READ, current=current)

    def test_readiness_uses_new_unpublished_secret_and_returns_only_evidence(self):
        self.save_encrypted()
        previous = slots(self.store)['fake-holdings']
        new_secret = 'replacement-input-secret'
        self.api.key = new_secret
        evidence = []

        def verify(candidate):
            self.assertEqual(slots(self.store)['fake-holdings'], previous)
            evidence.append(self.verifier()(candidate))

        def publish(db, candidate):
            # Receipt and metadata are visible together in the existing store.
            row = json.loads(db.execute("SELECT value FROM config WHERE key='api_slots'").fetchone()['value'])
            self.assertEqual(row['fake-holdings']['credential_ref'], candidate['credential_ref'])
            db.execute('INSERT INTO config VALUES (?,?)', ('synthetic_input_receipt',
                       json.dumps({'reference': candidate['credential_ref'], 'phase': 'published'})))

        self.save_encrypted(new_secret, _verify=verify, _publish=publish)
        self.assertEqual(self.api.calls[0]['headers']['Authorization'], 'Bearer ' + new_secret)
        self.assertEqual(evidence[0]['status'], 200)
        self.assertTrue(evidence[0]['subject_verified'])
        self.assertNotIn('data', evidence[0])
        self.assertNotIn(new_secret, json.dumps(evidence))
        self.assertEqual(self.store.config('synthetic_input_receipt')['reference'],
                         slots(self.store)['fake-holdings']['credential_ref'])

    def test_failed_readiness_preserves_old_connection_and_allows_corrected_input(self):
        self.save_encrypted()
        before = slots(self.store)
        secret_file = self.store.secret_path.read_text()
        for fault in ('no_auth', 'other_account', 'partial', 'mismatch', 'stale'):
            with self.subTest(fault=fault):
                self.api.fault = fault
                with self.assertRaises(ApiError):
                    self.save_encrypted(_verify=self.verifier())
                self.assertEqual(slots(self.store), before)
                self.assertEqual(self.store.secret_path.read_text(), secret_file)
        self.api.fault = None
        self.save_encrypted(_verify=self.verifier())
        self.assertGreater(slots(self.store)['fake-holdings']['revision'], before['fake-holdings']['revision'])

    def test_readiness_cannot_mutate_even_with_an_approval_surface(self):
        self.save_encrypted()
        before = slots(self.store)
        for probe in (ORDER, {**READ, 'body': '{}'}, {**READ, 'effect': 'payment'}):
            with self.subTest(probe=probe), self.assertRaises(ApiError):
                self.save_encrypted(_verify=self.verifier(probe))
        self.assertFalse(self.api.calls)
        self.assertEqual(slots(self.store), before)

    def test_cancellation_after_first_read_prevents_retry_and_publication(self):
        self.save_encrypted()
        before = slots(self.store)
        self.api.fault = 'timeout_once'

        def current():
            if self.api.calls:
                raise ApiError('synthetic canceled input', 'api_input_cancelled')

        with self.assertRaises(ApiError) as refused:
            self.save_encrypted(_verify=self.verifier(current=current))
        self.assertEqual(refused.exception.code, 'api_input_cancelled')
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(slots(self.store), before)

    def test_active_credential_change_during_backoff_stops_the_retry(self):
        self.save_encrypted()
        self.api.fault = 'timeout_once'
        api = ApiRequests(self.store, self.job_id, transport=self.api,
                          sleep=lambda _: self.save_encrypted('replacement-secret-value'))
        with self.assertRaises(ApiError) as refused:
            api.call(READ)
        self.assertEqual(refused.exception.code, 'needs_setup')
        self.assertEqual(len(self.api.calls), 1)

    def test_receipt_failure_rolls_back_metadata_and_discards_candidate(self):
        self.save_encrypted()
        before = slots(self.store)
        secret_file = self.store.secret_path.read_text()

        def publish(db, candidate):
            db.execute('INSERT INTO config VALUES (?,?)', ('synthetic_input_receipt', '{}'))
            raise ApiError('synthetic changed input', 'api_input_changed')

        with self.assertRaises(ApiError):
            self.save_encrypted(_verify=self.verifier(), _publish=publish)
        self.assertEqual(slots(self.store), before)
        self.assertIsNone(self.store.config('synthetic_input_receipt'))
        self.assertEqual(self.store.secret_path.read_text(), secret_file)

    def test_connection_changed_during_readiness_wins_over_late_input(self):
        self.save_encrypted()
        replacement = []
        verify = self.verifier()

        def verify_then_replace(candidate):
            verify(candidate)
            self.save_encrypted('concurrent-corrected-secret')
            replacement.append(slots(self.store)['fake-holdings'])

        with self.assertRaises(ApiError) as refused:
            self.save_encrypted(_verify=verify_then_replace)
        self.assertEqual(refused.exception.code, 'api_slot_changed')
        self.assertEqual(slots(self.store)['fake-holdings'], replacement[0])
        from personal_agent.api_credentials import ApiCredentials
        self.assertEqual(ApiCredentials(self.store).read('fake-holdings', replacement[0]),
                         'concurrent-corrected-secret')

    def test_cancelled_read_response_is_not_published(self):
        self.save_encrypted()
        before = slots(self.store)

        def current():
            if self.api.calls:
                raise ApiError('synthetic cancellation', 'api_input_cancelled')

        with self.assertRaises(ApiError):
            self.save_encrypted(_verify=self.verifier(current=current))
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(slots(self.store), before)

    def test_encrypted_registration_reuses_api_transport_and_context_redaction(self):
        from personal_agent.current_context import redact_known_secrets
        self.save_encrypted(money=True)
        self.assertEqual(self.store.secret('api_slot:fake-holdings'), '')
        self.assertNotIn(self.key, self.store.secret_path.read_text())
        self.assertNotIn(self.key, json.dumps(self.store.config('api_slots')))
        result = self.caps().execute('api_request', READ)
        self.assertTrue(result['provenance']['subject_verified'])
        self.assertEqual(redact_known_secrets(self.store, 'value=' + self.key), 'value=[redacted]')
        self.assertNotIn(self.key, json.dumps(self.store.task_events(self.job_id)))

    def test_metadata_failure_cannot_replace_previous_credential(self):
        from personal_agent.api_credentials import ApiCredentials
        self.save_encrypted()
        before = slots(self.store)['fake-holdings']
        secret_file = self.store.secret_path.read_text()
        with self.store.db() as db:
            db.execute("CREATE TRIGGER fail_slot_publish BEFORE UPDATE OF value ON config "
                       "WHEN NEW.key='api_slots' BEGIN SELECT RAISE(FAIL, 'synthetic failure'); END")
        with self.assertRaises(Exception):
            self.save_encrypted('new-synthetic-credential-value')
        self.assertEqual(slots(self.store)['fake-holdings'], before)
        self.assertEqual(ApiCredentials(self.store).read('fake-holdings', before), self.key)
        self.assertEqual(self.store.secret_path.read_text(), secret_file)

    def test_staged_and_retired_values_are_redacted_without_becoming_active(self):
        from personal_agent.api_credentials import ApiCredentials
        from personal_agent.current_context import redact_known_secrets
        self.save_encrypted()
        old = slots(self.store)['fake-holdings']
        self.save_encrypted('replacement-secret-value')
        active = slots(self.store)['fake-holdings']
        pending = ApiCredentials(self.store).stage('fake-holdings', old, 'pending-secret-value')
        self.assertEqual(slots(self.store)['fake-holdings'], active)
        self.assertEqual(redact_known_secrets(self.store,
            self.key + ' replacement-secret-value pending-secret-value'),
            '[redacted] [redacted] [redacted]')
        ApiCredentials(self.store).discard(pending)

    def test_concurrent_first_saves_share_one_key_without_losing_either_slot(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest import mock
        from personal_agent.api_credentials import ApiCredentials
        key = self.store.api_slot_key
        with mock.patch.object(key, 'create', wraps=key.create) as create:
            with ThreadPoolExecutor(2) as workers:
                futures = [workers.submit(save_slot, self.store, name, [HOST], 'secret-value-' + name,
                                          encrypted=True, money=False) for name in ('first', 'second')]
                [future.result() for future in futures]
            self.assertEqual(create.call_count, 1)
        for name in ('first', 'second'):
            self.assertEqual(ApiCredentials(self.store).read(name, slots(self.store)[name]), 'secret-value-' + name)

    def test_removal_withdraws_connection_and_current_encrypted_credential(self):
        from personal_agent.api_requests import remove_slot
        self.save_encrypted()
        reference = slots(self.store)['fake-holdings']['credential_ref']
        self.assertTrue(remove_slot(self.store, 'fake-holdings'))
        self.assertNotIn('fake-holdings', slots(self.store))
        self.assertEqual(self.store.secret(reference), '')

    def test_staging_without_key_preserves_existing_ciphertext_and_key_recovery(self):
        from personal_agent.api_credentials import ApiCredentials, CredentialError
        self.save_encrypted()
        original_key = self.store.api_slot_key.get()
        original_slot = slots(self.store)['fake-holdings']
        original_file = self.store.secret_path.read_text()
        self.store.api_slot_key.delete()
        with self.assertRaises(CredentialError):
            self.save_encrypted('new-synthetic-credential')
        self.assertIsNone(self.store.api_slot_key.get())
        self.assertEqual(self.store.secret_path.read_text(), original_file)
        self.assertEqual(slots(self.store)['fake-holdings'], original_slot)
        self.store.api_slot_key.key = original_key
        self.assertEqual(ApiCredentials(self.store).read('fake-holdings', original_slot), self.key)

    def test_concurrent_publication_is_not_overwritten(self):
        from unittest import mock
        from personal_agent.api_credentials import ApiCredentials
        self.save_encrypted()
        original = ApiCredentials.stage
        rival = []
        def stage(adapter, name, slot, secret):
            reference = original(adapter, name, slot, secret)
            if not rival:
                rival.append(True)
                self.save_encrypted('concurrent-synthetic-credential')
            return reference
        with mock.patch.object(ApiCredentials, 'stage', stage), self.assertRaises(ApiError) as failed:
            self.save_encrypted('stale-synthetic-credential')
        self.assertEqual(failed.exception.code, 'api_slot_changed')
        current = slots(self.store)['fake-holdings']
        self.assertEqual(ApiCredentials(self.store).read('fake-holdings', current), 'concurrent-synthetic-credential')

    def test_cleanup_failure_reports_published_state_instead_of_claiming_rollback(self):
        from unittest import mock
        with mock.patch.object(self.store, 'remove_secret', side_effect=OSError('synthetic')), self.assertRaises(ApiError) as failed:
            self.save_encrypted()
        self.assertEqual(failed.exception.code, 'api_secret_cleanup_pending')
        self.assertIn('credential_ref', slots(self.store)['fake-holdings'])
        self.assertIn('저장됐지만', str(failed.exception))

    def test_credential_cannot_be_rebound_to_changed_subject_or_destination(self):
        from personal_agent.api_credentials import ApiCredentials, CredentialError
        self.save_encrypted()
        slot = slots(self.store)['fake-holdings']
        for change in ({'hosts': ['other.example']}, {'subject': {'field': '$.account_id', 'value': 'another'}},
                       {'money': not slot['money']}, {'revision': slot['revision'] + 1}, {'header': 'X-Key'}):
            with self.subTest(change=change), self.assertRaises(CredentialError):
                ApiCredentials(self.store).read('fake-holdings', {**slot, **change})
        with self.assertRaises(ApiError):
            save_slot(self.store, 'fake-holdings', [HOST], 'unencrypted-replacement')

    def test_missing_key_fails_without_recreating_key_or_calling_provider(self):
        from personal_agent.api_credentials import CredentialError
        from personal_agent.current_context import redact_known_secrets
        self.save_encrypted()
        self.store.api_slot_key.delete()
        from personal_agent.api_requests import ApiRequests
        with self.assertRaises(ApiError) as refused:
            ApiRequests(self.store, self.job_id).call(READ)
        self.assertEqual(refused.exception.code, 'needs_setup')
        self.assertIsNone(self.store.api_slot_key.get())
        self.assertFalse(self.api.calls)
        with self.assertRaises(CredentialError):
            redact_known_secrets(self.store, self.key)

    def test_in_flight_revision_change_withholds_read_and_marks_change_unknown(self):
        from personal_agent.api_requests import ApiRequests
        self.save_encrypted(money=True)
        def changed(*args):
            result = self.api(*args)
            self.save_encrypted('replacement-synthetic-credential', money=True)
            return result
        api = ApiRequests(self.store, self.job_id, approvals=self.approvals, transport=changed)
        with self.assertRaises(ApiError) as refused:
            api.call(READ)
        self.assertEqual(refused.exception.code, 'needs_setup')
        self.save_encrypted(money=True)
        with self.assertRaises(ApiError):
            api.call(ORDER)
        self.approvals.approve_last()
        with self.assertRaises(ApiError) as unknown:
            api.call(ORDER)
        self.assertEqual(unknown.exception.effect, 'unknown')
        self.assertEqual(len(self.api.orders), 1)

    def test_key_loss_after_approved_dispatch_withholds_response_as_unknown(self):
        from personal_agent.api_requests import ApiRequests
        self.save_encrypted(money=True)
        def lost_key(*args):
            result = self.api(*args)
            self.store.api_slot_key.delete()
            return result
        api = ApiRequests(self.store, self.job_id, approvals=self.approvals, transport=lost_key)
        with self.assertRaises(ApiError):
            api.call(ORDER)
        self.approvals.approve_last()
        with self.assertRaises(ApiError) as unknown:
            api.call(ORDER)
        self.assertEqual(unknown.exception.code, 'api_redaction_unavailable')
        self.assertEqual(unknown.exception.effect, 'unknown')
        self.assertEqual(len(self.api.orders), 1)
