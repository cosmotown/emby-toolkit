import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import config_manager  # Initialize settings before importing the proxy.
import reverse_proxy


class VidHubTraceTests(unittest.TestCase):
    def setUp(self):
        self.trace_env = patch.dict(os.environ, {'VIDHUB_TRACE_ENABLED': '1'}, clear=False)
        self.trace_env.start()
        self.config = patch.dict(
            reverse_proxy.config_manager.APP_CONFIG,
            {
                'emby_server_url': 'http://isolated-emby:8096',
                'emby_api_key': 'server-secret-key',
                'proxy_merge_native_libraries': True,
                'proxy_native_view_selection': 'native-1',
                'proxy_native_view_order': 'before',
            },
            clear=False,
        )
        self.config.start()
        self.client = reverse_proxy.proxy_app.test_client()

    def tearDown(self):
        self.config.stop()
        self.trace_env.stop()

    def test_views_trace_records_request_counts_and_view_fields_without_secrets(self):
        native = {
            'Id': 'native-1',
            'Name': 'Native Movies',
            'Type': 'CollectionFolder',
            'CollectionType': 'movies',
            'ServerId': 'server-1',
            'ParentId': '2',
            'IsFolder': True,
            'ImageTags': {},
        }
        collection = {
            'id': 7,
            'name': '电影',
            'emby_collection_id': 'boxset-7',
            'definition_json': {'item_type': ['Movie']},
            'in_library_count': 12,
        }

        with patch.object(reverse_proxy.extensions, 'EMBY_SERVER_ID', 'server-1'), \
             patch.object(reverse_proxy.emby, 'get_emby_libraries', return_value=[native]), \
             patch.object(
                 reverse_proxy.custom_collection_db,
                 'get_all_active_custom_collections',
                 return_value=[collection],
             ), self.assertLogs(reverse_proxy.logger, level='INFO') as captured:
            response = self.client.get(
                '/emby/Users/abcdef/Views',
                query_string={
                    'ParentId': '-900007',
                    'IncludeItemTypes': 'Movie,Series',
                    'Recursive': 'true',
                    'Fields': 'ImageTags,Path',
                    'SortBy': 'SortName',
                    'CollectionType': 'movies',
                    'api_key': 'query-secret-key',
                    'X-Emby-Token': 'query-secret-token',
                },
                headers={
                    'User-Agent': 'VidHub/Test',
                    'X-Emby-Token': 'header-secret-token',
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['TotalRecordCount'], 2)
        output = '\n'.join(captured.output)
        self.assertIn('[VIDHUB_TRACE]', output)
        self.assertIn('"client_hint":"vidhub"', output)
        self.assertIn('"items_count":2', output)
        self.assertIn('"total_record_count":2', output)
        self.assertIn('"event":"view_item"', output)
        self.assertIn('"Name":"Native Movies"', output)
        self.assertIn('"Name":"电影"', output)
        self.assertIn('api_key=%3Credacted%3E', output)
        self.assertIn('X-Emby-Token=%3Credacted%3E', output)
        self.assertNotIn('query-secret-key', output)
        self.assertNotIn('query-secret-token', output)
        self.assertNotIn('header-secret-token', output)
        self.assertNotIn('server-secret-key', output)

    def test_unknown_user_agent_remains_traceable_without_guessing_client(self):
        with patch.object(reverse_proxy.extensions, 'EMBY_SERVER_ID', 'server-1'), \
             patch.object(reverse_proxy.emby, 'get_emby_libraries', return_value=[]), \
             patch.object(
                 reverse_proxy.custom_collection_db,
                 'get_all_active_custom_collections',
                 return_value=[],
             ), self.assertLogs(reverse_proxy.logger, level='INFO') as captured:
            response = self.client.get(
                '/emby/Users/abcdef/Views',
                headers={'User-Agent': 'UnknownClient/1.0'},
            )

        self.assertEqual(response.status_code, 200)
        output = '\n'.join(captured.output)
        self.assertIn('"client_hint":"unclassified"', output)
        self.assertIn('"user_agent":"UnknownClient/1.0"', output)

    def test_nginx_trace_uses_allowlisted_query_fields_and_routes_catalogue_reads(self):
        template = (
            Path(__file__).resolve().parents[1]
            / 'templates'
            / 'nginx'
            / 'emby_proxy.conf.template'
        ).read_text(encoding='utf-8')

        self.assertIn('log_format vidhub_trace', template)
        self.assertIn('access_log /dev/stdout vidhub_trace', template)
        self.assertIn('location = /emby/Library/VirtualFolders', template)
        self.assertIn('location = /Library/VirtualFolders', template)
        self.assertIn('location = /emby/Items', template)
        self.assertIn('location = /Items', template)
        self.assertNotIn('$args', template)
        self.assertNotIn('$query_string', template)
        self.assertNotIn('$http_x_emby_token', template.lower())
        self.assertNotIn('$arg_api_key', template.lower())

    def test_native_views_upstream_auth_uses_header_and_failure_log_is_path_free(self):
        response = Mock()
        response.json.return_value = {'Items': []}
        with patch.object(reverse_proxy.emby.logger, 'trace', create=True), \
             patch.object(reverse_proxy.emby.emby_client, 'get', return_value=response) as get:
            self.assertEqual(
                reverse_proxy.emby.get_emby_libraries(
                    'http://isolated-emby:8096',
                    'upstream-secret-token',
                    'abcdef',
                ),
                [],
            )

        self.assertEqual(
            get.call_args.kwargs['headers'],
            {'X-Emby-Token': 'upstream-secret-token'},
        )
        self.assertNotIn('params', get.call_args.kwargs)

        request_error = reverse_proxy.requests.exceptions.RequestException(
            'https://example.invalid/Views?api_key=must-not-log'
        )
        with patch.object(reverse_proxy.emby.logger, 'trace', create=True), \
             patch.object(reverse_proxy.emby.emby_client, 'get', side_effect=request_error), \
             self.assertLogs(reverse_proxy.emby.logger, level='ERROR') as captured:
            result = reverse_proxy.emby.get_emby_libraries(
                'http://isolated-emby:8096',
                'upstream-secret-token',
                'abcdef',
            )

        self.assertIsNone(result)
        output = '\n'.join(captured.output)
        self.assertIn('error_type=RequestException', output)
        self.assertNotIn('must-not-log', output)
        self.assertNotIn('upstream-secret-token', output)


if __name__ == '__main__':
    unittest.main()
