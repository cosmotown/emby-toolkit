import logging
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

if not hasattr(logging.Logger, 'trace'):
    logging.Logger.trace = logging.Logger.debug

import config_manager
from database import person_cleanup_db
from handler import emby
from tasks import actors


def response(payload=None, status=200, json_error=None):
    value = Mock(status_code=status)
    if json_error is not None:
        value.json.side_effect = json_error
    else:
        value.json.return_value = payload
    return value


def valid_payload(**changes):
    payload = {
        'Id': 'm1',
        'Type': 'Movie',
        'Path': '/normal/movie.mkv',
        'People': [{'Id': 'B', 'Name': 'Live B'}],
    }
    payload.update(changes)
    return payload


def ready_result():
    return {
        'forensic_state': 'verified_stale_index_signature',
        'identity_signal': 'stale_index_no_identity_owner',
        'people_signal': 'stale_index_different_people',
        'query_count': 1,
        'actual_people_count': 1,
        'same_name_other_count': 0,
        'identity_owner_count': 0,
    }


class StrictItemPeopleHelperTests(unittest.TestCase):
    def test_user_scoped_endpoint_is_the_only_endpoint_and_returns_people(self):
        def fake_get(url, **kwargs):
            if '/Users/user-1/Items/m1' in url:
                return response(valid_payload())
            return response({'Id': 'm1', 'Type': 'Movie', 'Path': '/normal/movie.mkv'})

        with patch.object(emby.emby_client, 'get', side_effect=fake_get) as get:
            result = emby.get_item_people_detail_strict(
                'http://emby', 'secret-token', 'user-1', 'm1',
            )

        self.assertEqual(result['People'], [('B', 'Live B')])
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], 'http://emby/Users/user-1/Items/m1')
        self.assertEqual(get.call_args.kwargs['params'], {'Fields': 'People,Path,Type'})
        self.assertEqual(get.call_args.kwargs['headers'], {'X-Emby-Token': 'secret-token'})
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def assert_failure(self, payload, reason, *, status=200, json_error=None):
        with patch.object(
            emby.emby_client, 'get',
            return_value=response(payload, status=status, json_error=json_error),
        ):
            with self.assertRaises(emby.ItemPeopleDetailError) as raised:
                emby.get_item_people_detail_strict('http://emby', 'token', 'user', 'm1')
        self.assertEqual(raised.exception.diagnostic['reason'], reason)
        self.assertNotIn('token', str(raised.exception))
        return raised.exception.diagnostic

    def test_http_and_json_failures_are_distinct(self):
        diagnostic = self.assert_failure({}, 'exact_item_http_failure', status=500)
        self.assertEqual(diagnostic['http_status'], 500)
        self.assert_failure(None, 'exact_item_json_invalid')
        self.assert_failure(None, 'exact_item_json_invalid', json_error=ValueError('bad body'))

    def test_identity_and_type_shape_failures_are_distinct(self):
        self.assert_failure(valid_payload(Id='other'), 'exact_item_id_mismatch')
        self.assert_failure(valid_payload(Type=''), 'exact_item_type_mismatch')

    def test_people_shape_failure_matrix_is_fail_closed(self):
        fixtures = (
            ({key: value for key, value in valid_payload().items() if key != 'People'}, 'people_missing'),
            (valid_payload(People={}), 'people_not_list'),
            (valid_payload(People=[]), 'people_empty'),
            (valid_payload(People=['invalid']), 'people_row_invalid'),
            (valid_payload(People=[{'Name': 'No ID'}]), 'people_id_missing'),
            (valid_payload(People=[{'Id': 'B'}]), 'people_name_missing'),
        )
        for payload, reason in fixtures:
            with self.subTest(reason=reason):
                self.assert_failure(payload, reason)

    def test_request_exception_is_safe_and_has_no_fallback(self):
        with patch.object(emby.emby_client, 'get', side_effect=RuntimeError('token=secret')) as get:
            with self.assertRaises(emby.ItemPeopleDetailError) as raised:
                emby.get_item_people_detail_strict('http://emby', 'secret', 'user', 'm1')
        self.assertEqual(raised.exception.diagnostic['reason'], 'exact_item_http_failure')
        self.assertEqual(get.call_count, 1)
        self.assertNotIn('secret', str(raised.exception))


class CanaryStrictPeopleContractTests(unittest.TestCase):
    def setUp(self):
        self.roots = {
            'complete': True,
            'roots': ({
                'library_id': 'L', 'library_name': 'Normal',
                'style': 'posix', 'path': '/normal',
            },),
        }
        self.snapshot = {
            'all_roots': self.roots,
            'item_people': {
                'm1': {
                    'item_id': 'm1', 'item_type': 'Movie', 'library_id': 'L',
                    'people': (('B', 'Live B'),),
                },
            },
            'normal_ids': {'B'},
            'identity_index': {},
            'root_contract': {},
            'contract': {},
        }
        self.hit = {'Id': 'm1', 'Type': 'Movie', 'Path': '/normal/movie.mkv'}
        self.source = {
            'person_id': 'A', 'person_name': 'Candidate A', 'provider_ids': {},
            'candidate_fingerprint': 'fingerprint-A',
            'source_proof_state': 'identity_not_found',
        }
        self.processor = SimpleNamespace(
            emby_url='http://emby', emby_api_key='token', emby_user_id='user-1',
        )

    def run_check(self, detail):
        with patch.object(actors.emby, 'get_person_media_query_items_strict', return_value=[self.hit]), \
                patch.object(actors.emby, 'get_item_people_detail_strict', return_value=detail) as strict, \
                patch.object(person_cleanup_db, 'get_candidates_by_ids', return_value=[{'person_id': 'A'}]), \
                patch.object(actors.emby, 'get_person_detail_forensic_strict', return_value={'status': 'ok'}), \
                patch.object(actors, 'classify_stale_index_forensic', return_value=ready_result()), \
                patch.object(person_cleanup_db, 'candidate_protection_reason', return_value=None):
            result = actors._check_stale_delete_canary_candidate(
                self.processor, self.source, self.snapshot,
            )
        strict.assert_called_once_with('http://emby', 'token', 'user-1', 'm1')
        return result

    def test_valid_fresh_exact_detail_can_be_ready(self):
        self.assertEqual(
            self.run_check({
                'Id': 'm1', 'Type': 'Movie', 'Path': '/normal/movie.mkv',
                'People': [('B', 'Live B')],
            })['forensic_state'],
            'verified_stale_index_signature',
        )

    def test_type_ownership_and_people_drift_all_fail_closed(self):
        variants = (
            ({'Id': 'm1', 'Type': 'Episode', 'Path': '/normal/movie.mkv', 'People': [('B', 'Live B')]}, 'relationship_drift', 'exact_item_type_mismatch'),
            ({'Id': 'm1', 'Type': 'Movie', 'Path': '/other/movie.mkv', 'People': [('B', 'Live B')]}, 'relationship_drift', 'exact_item_ownership_mismatch'),
            ({'Id': 'm1', 'Type': 'Movie', 'Path': '/normal/movie.mkv', 'People': [('C', 'Changed')]}, 'relationship_drift', 'exact_item_people_mismatch'),
            ({'Id': 'm1', 'Type': 'Movie', 'Path': '/normal/movie.mkv', 'People': [('A', 'Candidate A')]}, 'linked', None),
        )
        for detail, state, reason in variants:
            with self.subTest(state=state):
                with patch.object(actors.emby, 'get_person_media_query_items_strict', return_value=[self.hit]), \
                        patch.object(actors.emby, 'get_item_people_detail_strict', return_value=detail):
                    with self.assertRaises(person_cleanup_db.CanarySafetyError) as raised:
                        actors._check_stale_delete_canary_candidate(
                            self.processor, self.source, self.snapshot,
                        )
                self.assertEqual(raised.exception.state, state)
                if reason:
                    self.assertEqual(raised.exception.diagnostic['reason'], reason)

    def test_strict_failure_is_persistable_safe_diagnostic(self):
        strict_error = emby.ItemPeopleDetailError(
            'people_missing', item_id='m1', item_type='Movie',
            http_status=200, people_present=False, people_count=None,
        )
        with patch.object(actors.emby, 'get_person_media_query_items_strict', return_value=[self.hit]), \
                patch.object(actors.emby, 'get_item_people_detail_strict', side_effect=strict_error), \
                self.assertLogs(actors.logger, level='WARNING') as captured:
            with self.assertRaises(person_cleanup_db.CanarySafetyError) as raised:
                actors._check_stale_delete_canary_candidate(
                    self.processor, self.source, self.snapshot,
                )
        self.assertEqual(raised.exception.state, 'people_unavailable')
        self.assertEqual(raised.exception.diagnostic, {
            'person_id': 'A', 'item_id': 'm1', 'item_type': 'Movie',
            'http_status': 200, 'reason': 'people_missing',
            'people_present': False, 'people_count': None,
        })
        text = '\n'.join(captured.output)
        self.assertIn('reason=people_missing', text)
        self.assertNotIn('token', text)
        self.assertNotIn('/normal/movie.mkv', text)


class CanaryPreviewStrictPeopleTests(unittest.TestCase):
    def exercise(self, failure_at=None):
        items = [{'person_id': str(index)} for index in range(1, 11)]
        snap = {
            'generation': 1,
            'protection_hash': 'p',
            'normal_people_relationship_hash': 'r',
            'person_hash': 'i',
            'item_people': {},
            'person_details': {},
        }
        processor = SimpleNamespace(
            emby_url='http://emby', emby_api_key='token', emby_user_id='user',
            is_stop_requested=Mock(return_value=False),
        )
        results = []

        def check(_processor, source, _snapshot):
            if source['person_id'] == str(failure_at):
                raise person_cleanup_db.CanarySafetyError(
                    'people_unavailable', 'exact People failed',
                    {'person_id': source['person_id'], 'item_id': 'm1',
                     'item_type': 'Movie', 'http_status': 200,
                     'reason': 'people_empty', 'people_present': True,
                     'people_count': 0},
                )
            return ready_result()

        def mark(_job_id, person_id, state, evidence=None, error=None):
            results.append((person_id, state, evidence, error))
            return True

        with patch.object(person_cleanup_db, 'validate_stale_delete_canary_chain'), \
                patch.object(actors.emby, 'ensure_admin_delete_context', return_value=SimpleNamespace(binding_hash='binding')), \
                patch.object(person_cleanup_db, 'bind_stale_delete_canary_admin_context'), \
                patch.object(actors, '_build_stale_delete_canary_snapshot', side_effect=[deepcopy(snap), deepcopy(snap)]), \
                patch.object(person_cleanup_db, 'set_stale_delete_canary_preview_snapshot'), \
                patch.object(person_cleanup_db, 'list_stale_delete_canary_items', return_value=items), \
                patch.object(person_cleanup_db, 'stale_delete_canary_stop_requested', return_value=False), \
                patch.object(actors, '_check_stale_delete_canary_candidate', side_effect=check) as strict_check, \
                patch.object(person_cleanup_db, 'mark_stale_delete_canary_preview_item', side_effect=mark), \
                patch.object(person_cleanup_db, 'finish_stale_delete_canary_preview') as finish, \
                patch.object(person_cleanup_db, 'fail_stale_delete_canary_job') as fail, \
                patch.object(actors.task_manager, 'update_status_from_thread'), \
                patch.object(actors.emby.emby_client, 'post_once') as post, \
                patch.object(actors.emby, 'delete_person_custom_api_outcome') as delete:
            actors.task_preview_stale_delete_canary(processor, 'job-1')

        self.assertEqual(strict_check.call_count, 10)
        finish.assert_called_once_with('job-1')
        fail.assert_not_called()
        post.assert_not_called()
        delete.assert_not_called()
        return results

    def test_ten_valid_candidates_are_all_marked_ready_get_only(self):
        results = self.exercise()
        self.assertEqual([state for _, state, _, _ in results], ['canary_delete_ready'] * 10)

    def test_one_strict_failure_is_rejected_with_diagnostic_and_zero_mutation(self):
        results = self.exercise(failure_at=6)
        failed = results[5]
        self.assertEqual(failed[1], 'preflight_rejected')
        self.assertEqual(failed[2]['exact_item_people']['reason'], 'people_empty')
        self.assertEqual(sum(state == 'canary_delete_ready' for _, state, _, _ in results), 9)


if __name__ == '__main__':
    unittest.main()
