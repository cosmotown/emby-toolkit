import unittest

from routes.person_cleanup import _serialize_stale_delete_canary_job


class StaleDeleteCanaryUiContractTests(unittest.TestCase):
    def test_selected_total_is_the_backend_fixed_candidate_total(self):
        payload = _serialize_stale_delete_canary_job({
            'job_id': 'job-1',
            'requested_limit': 10,
            'candidate_total': 8,
            'confirmation_token_hash': 'secret',
        })
        self.assertEqual(payload['requested_limit'], 10)
        self.assertEqual(payload['selected_total'], 8)
        self.assertNotIn('confirmation_token_hash', payload)

    def test_empty_job_is_preserved(self):
        self.assertIsNone(_serialize_stale_delete_canary_job(None))


if __name__ == '__main__':
    unittest.main()
