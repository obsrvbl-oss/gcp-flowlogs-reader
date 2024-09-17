from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
from ipaddress import ip_address
from io import StringIO
from unittest import TestCase
from unittest.mock import MagicMock, patch, call
from tempfile import NamedTemporaryFile

from gcp_flowlogs_reader.gcp_flowlogs_reader import BASE_LOG_NAME
from google.api_core.exceptions import GoogleAPIError, PermissionDenied, NotFound
from google.cloud.logging import Client
from google.cloud.logging.entries import StructEntry
from google.cloud.logging.resource import Resource
from google.oauth2.service_account import Credentials

from gcp_flowlogs_reader.aggregation import aggregated_records
import gcp_flowlogs_reader.__main__ as cli_module
from gcp_flowlogs_reader import (
    FlowRecord,
    Reader,
    InstanceDetails,
    VpcDetails,
    GeographicDetails,
    ResourceLabels,
)
from gcp_flowlogs_reader.gcp_flowlogs_reader import safe_tuple_from_dict


PREFIX = 'gcp_flowlogs_reader.gcp_flowlogs_reader.{}'.format
SAMPLE_PAYLOADS = [
    {
        'bytes_sent': '491',
        'connection': {
            'dest_ip': '192.0.2.2',
            'dest_port': 3389.0,
            'protocol': 6.0,
            'src_ip': '198.51.100.75',
            'src_port': 49444.0,
        },
        'dest_instance': {
            'project_id': 'yoyodyne-102010',
            'region': 'us-west1',
            'vm_name': 'vm-instance-01',
            'zone': 'us-west1-a',
        },
        'dest_vpc': {
            'project_id': 'yoyodyne-102010',
            'subnetwork_name': 'yoyo-vpc-1',
            'subnetwork_region': 'sunnydale1',
            'vpc_name': 'yoyo-vpc-1',
        },
        'end_time': '2018-04-03T13:47:38.401Z',
        'packets_sent': '4',
        'reporter': 'DEST',
        'src_location': {
            'city': 'Santa Teresa',
            'continent': 'America',
            'country': 'usa',
            'region': 'California',
        },
        'start_time': '2018-04-03T13:47:37.301723960Z',
        'rtt_msec': '61',
    },
    {
        'bytes_sent': '756',
        'connection': {
            'dest_ip': '198.51.100.75',
            'dest_port': 49444.0,
            'protocol': 6.0,
            'src_ip': '192.0.2.2',
            'src_port': 3389.0,
        },
        'dest_location': {
            'city': 'Santa Teresa',
            'continent': 'America',
            'country': 'usa',
            'region': 'California',
        },
        'end_time': '2018-04-03T13:47:33.937764566Z',
        'packets_sent': '6',
        'reporter': 'SRC',
        'src_instance': {
            'project_id': 'yoyodyne-102010',
            'region': 'us-west1',
            'vm_name': 'vm-instance-01',
            'zone': 'us-west1-a',
        },
        'src_vpc': {
            'project_id': 'yoyodyne-102010',
            'subnetwork_name': 'yoyo-vpc-1',
            'subnetwork_region': 'sunnydale2',
            'vpc_name': 'yoyo-vpc-1',
        },
        'start_time': '2018-04-03T13:47:32.805417512Z',
    },
    {
        'bytes_sent': '1020',
        'connection': {
            'dest_ip': '192.0.2.3',
            'dest_port': 65535.0,
            'protocol': 6.0,
            'src_ip': '192.0.2.2',
            'src_port': 3389.0,
        },
        'end_time': '2018-04-03T13:48:33.937764566Z',
        'packets_sent': '20',
        'reporter': 'SRC',
        'src_instance': {
            'project_id': 'yoyodyne-102010',
            'region': 'us-west1',
            'vm_name': 'vm-instance-01',
            'zone': 'us-west1-a',
        },
        'src_vpc': {
            'project_id': 'yoyodyne-102010',
            'subnetwork_name': 'yoyo-vpc-1',
            'vpc_name': 'yoyo-vpc-1',
        },
        'dest_instance': {
            'project_id': 'yoyodyne-102010',
            'region': 'us-west1',
            'vm_name': 'vm-instance-02',
            'zone': 'us-west1-a',
        },
        'dest_vpc': {
            'project_id': 'yoyodyne-102010',
            'subnetwork_name': 'yoyo-vpc-1',
            'vpc_name': 'yoyo-vpc-1',
        },
        'start_time': '2018-04-03T13:47:31.805417512Z',
    },
    {
        'bytes_sent': '1020',
        'connection': {
            'dest_ip': '192.0.2.3',
            'protocol': 1.0,
            'src_ip': '192.0.2.2',
        },
        'end_time': '2018-04-03T13:48:33.937764566Z',
        'packets_sent': '20',
        'reporter': 'SRC',
        'src_instance': {
            'project_id': 'yoyodyne-102010',
            'region': 'us-west1',
            'vm_name': 'vm-instance-01',
            'zone': 'us-west1-a',
        },
        'src_vpc': {
            'project_id': 'yoyodyne-102010',
            'subnetwork_name': 'yoyo-vpc-1',
            'vpc_name': 'yoyo-vpc-1',
        },
        'dest_instance': {
            'project_id': 'yoyodyne-102010',
            'region': 'us-west1',
            'vm_name': 'vm-instance-02',
            'zone': 'us-west1-a',
        },
        'dest_vpc': {
            'project_id': 'yoyodyne-102010',
            'subnetwork_name': 'yoyo-vpc-1',
            'vpc_name': 'yoyo-vpc-1',
        },
    },
]

SAMPLE_ENTRIES = [StructEntry(x, None) for x in SAMPLE_PAYLOADS]


class MockIterator:
    def __init__(self):
        self.pages = (
            [SAMPLE_ENTRIES[0], SAMPLE_ENTRIES[1]],
            [SAMPLE_ENTRIES[2], SAMPLE_ENTRIES[3]],
        )
        self.next_page_token = None

    def __iter__(self):
        return self

    def __next__(self):
        return ''


class MockFailedIterator:
    def __init__(self):
        self.pages = self

    def __iter__(self):
        return self

    def __next__(self):
        raise PermissionDenied('403 The caller does not have permission')


class MockNotFoundIterator:
    def __init__(self):
        self.pages = self

    def __iter__(self):
        return self

    def __next__(self):
        raise NotFound('404 Project does not exist: project-name')


class TestClient(Client):
    _credentials = ''


class FlowRecordTests(TestCase):
    def test_init_outbound(self):
        flow_record = FlowRecord(SAMPLE_ENTRIES[0])
        for attr, expected in [
            ('src_ip', ip_address('198.51.100.75')),
            ('src_port', 49444),
            ('dest_ip', ip_address('192.0.2.2')),
            ('dest_port', 3389),
            ('protocol', 6),
            ('start_time', datetime(2018, 4, 3, 13, 47, 37)),
            ('end_time', datetime(2018, 4, 3, 13, 47, 38)),
            ('bytes_sent', 491),
            ('packets_sent', 4),
            ('rtt_msec', 61),
            ('reporter', 'DEST'),
            ('src_instance', None),
            (
                'dest_instance',
                safe_tuple_from_dict(
                    InstanceDetails, SAMPLE_PAYLOADS[0]['dest_instance']
                ),
            ),
            ('src_vpc', None),
            (
                'dest_vpc',
                safe_tuple_from_dict(VpcDetails, SAMPLE_PAYLOADS[0]['dest_vpc']),
            ),
            (
                'src_location',
                safe_tuple_from_dict(
                    GeographicDetails, SAMPLE_PAYLOADS[0]['src_location']
                ),
            ),
            ('dest_location', None),
        ]:
            with self.subTest(attr=attr):
                actual = getattr(flow_record, attr)
                self.assertEqual(actual, expected)
                self.assertEqual(type(actual), type(expected))

    def test_init_inbound(self):
        flow_record = FlowRecord(SAMPLE_ENTRIES[1])
        for attr, expected in [
            ('src_ip', ip_address('192.0.2.2')),
            ('src_port', 3389),
            ('dest_ip', ip_address('198.51.100.75')),
            ('dest_port', 49444),
            ('protocol', 6),
            ('start_time', datetime(2018, 4, 3, 13, 47, 32)),
            ('end_time', datetime(2018, 4, 3, 13, 47, 33)),
            ('bytes_sent', 756),
            ('packets_sent', 6),
            ('rtt_msec', None),
            ('reporter', 'SRC'),
            (
                'src_instance',
                safe_tuple_from_dict(
                    InstanceDetails, SAMPLE_PAYLOADS[1]['src_instance']
                ),
            ),
            ('dest_instance', None),
            (
                'src_vpc',
                safe_tuple_from_dict(VpcDetails, SAMPLE_PAYLOADS[1]['src_vpc']),
            ),
            ('dest_vpc', None),
            ('src_location', None),
            (
                'dest_location',
                safe_tuple_from_dict(
                    GeographicDetails, SAMPLE_PAYLOADS[1]['dest_location']
                ),
            ),
        ]:
            with self.subTest(attr=attr):
                actual = getattr(flow_record, attr)
                self.assertEqual(actual, expected)
                self.assertEqual(type(actual), type(expected))

    def test_eq(self):
        self.assertEqual(FlowRecord(SAMPLE_ENTRIES[0]), FlowRecord(SAMPLE_ENTRIES[0]))
        self.assertNotEqual(
            FlowRecord(SAMPLE_ENTRIES[0]), FlowRecord(SAMPLE_ENTRIES[1])
        )
        self.assertNotEqual(FlowRecord(SAMPLE_ENTRIES[0]), SAMPLE_ENTRIES[0])

    def test_hash(self):
        self.assertEqual(
            hash(FlowRecord(SAMPLE_ENTRIES[0])),
            hash(FlowRecord(SAMPLE_ENTRIES[0])),
        )
        self.assertNotEqual(
            hash(FlowRecord(SAMPLE_ENTRIES[0])),
            hash(FlowRecord(SAMPLE_ENTRIES[1])),
        )

    def test_repr(self):
        actual = repr(FlowRecord(SAMPLE_ENTRIES[0]))
        expected = '<FlowRecord 198.51.100.75:49444/6->192.0.2.2:3389/6>'
        self.assertEqual(actual, expected)

    def test_str(self):
        actual = str(FlowRecord(SAMPLE_ENTRIES[0]))
        expected = (
            'src_ip: 198.51.100.75, '
            'src_port: 49444, '
            'dest_ip: 192.0.2.2, '
            'dest_port: 3389, '
            'protocol: 6, '
            'start_time: 2018-04-03 13:47:37, '
            'end_time: 2018-04-03 13:47:38, '
            'bytes_sent: 491, '
            'packets_sent: 4'
        )
        self.assertEqual(actual, expected)

    def test_to_dict(self):
        flow_dict = FlowRecord(SAMPLE_ENTRIES[0]).to_dict()
        for attr, expected in [
            ('src_ip', ip_address('198.51.100.75')),
            ('src_port', 49444),
            ('dest_ip', ip_address('192.0.2.2')),
            ('dest_port', 3389),
            ('protocol', 6),
            ('start_time', datetime(2018, 4, 3, 13, 47, 37)),
            ('end_time', datetime(2018, 4, 3, 13, 47, 38)),
            ('bytes_sent', 491),
            ('packets_sent', 4),
            ('rtt_msec', 61),
            ('reporter', 'DEST'),
            ('src_instance', None),
            ('dest_instance', SAMPLE_PAYLOADS[0]['dest_instance']),
            ('src_vpc', None),
            ('dest_vpc', SAMPLE_PAYLOADS[0]['dest_vpc']),
            ('src_location', SAMPLE_PAYLOADS[0]['src_location']),
            ('dest_location', None),
        ]:
            with self.subTest(attr=attr):
                actual = flow_dict[attr]
                if isinstance(expected, dict):
                    expected = {
                        k: v for k, v in expected.items() if k != 'subnetwork_region'
                    }
                self.assertEqual(actual, expected)

    def test_from_payload(self):
        self.assertEqual(
            FlowRecord.from_payload(SAMPLE_PAYLOADS[0]),
            FlowRecord(SAMPLE_ENTRIES[0]),
        )

    def test_resource_labels(self):
        labels = {
            'location': 'us-central1-a',
            'project_id': 'proj1',
            'subnetwork_id': '3301803660181826306',
            'subnetwork_name': 'default',
        }

        resource = Resource(type='gcp_subnetwork', labels=labels)
        entry = StructEntry(SAMPLE_PAYLOADS[0], None, resource=resource)
        self.assertEqual(FlowRecord(entry).resource_labels, ResourceLabels(**labels))


@patch(PREFIX('LoggingClient'), autospec=TestClient)
class ReaderTests(TestCase):
    def test_init_with_client(self, MockLoggingClient):
        logging_client = MagicMock(Client)
        logging_client.project = 'yoyodyne-102010'
        reader = Reader(logging_client=logging_client)
        self.assertEqual(MockLoggingClient.call_count, 0)
        self.assertIs(reader.logging_client, logging_client)

    @patch(PREFIX('Credentials'), autospec=True)
    def test_init_with_credentials_info(self, MockCredentials, MockLoggingClient):
        creds = MagicMock(Credentials)
        creds.project_id = 'proj1'
        MockCredentials.from_service_account_info.return_value = creds

        client = MagicMock(Client)
        client.project = 'yoyodyne-102010'
        MockLoggingClient.return_value = client

        Reader(service_account_info={'foo': 1})

        MockCredentials.from_service_account_info.assert_called_once_with({'foo': 1})
        MockLoggingClient.assert_called_once_with(project='proj1', credentials=creds)

    @patch(PREFIX('Credentials'), autospec=True)
    def test_init_with_credentials_info_and_project(
        self, MockCredentials, MockLoggingClient
    ):
        # The credentials file specifies one project_id
        creds = MagicMock(Credentials)
        creds.project_id = 'proj1'
        MockCredentials.from_service_account_info.return_value = creds

        # The client has another one, which will be ignored
        client = MagicMock(Client)
        client.project = 'proj2'
        MockLoggingClient.return_value = client

        # The request is for a third one, which we'll use
        Reader(service_account_info={'foo': 1}, project='proj3')

        MockCredentials.from_service_account_info.assert_called_once_with({'foo': 1})
        MockLoggingClient.assert_called_once_with(project='proj3', credentials=creds)

    def test_init_with_credentials_json(self, MockLoggingClient):
        with NamedTemporaryFile() as temp_file:
            path = temp_file.name
            Reader(service_account_json=path, project='yoyodyne-102010')

        MockLoggingClient.from_service_account_json.assert_called_once_with(
            path, project='yoyodyne-102010'
        )

    def test_init_with_environment(self, MockLoggingClient):
        MockLoggingClient.return_value.project = 'yoyodyne-102010'
        Reader(project='yoyodyne-102010')
        MockLoggingClient.assert_called_once_with(project='yoyodyne-102010')

    def test_init_log_list(self, MockLoggingClient):
        MockLoggingClient.return_value.project = 'yoyodyne-1020'

        # Nothing specified - log name is derived from the project name
        normal_reader = Reader()
        self.assertEqual(
            normal_reader.log_list,
            ['projects/yoyodyne-1020/logs/compute.googleapis.com%2Fvpc_flows'],
        )

        # Custom name specified - log name is added to log list
        custom_reader = Reader(log_name='custom-log')
        self.assertEqual(custom_reader.log_list, ['custom-log'])

    def test_init_times(self, MockLoggingClient):
        MockLoggingClient.return_value.project = 'yoyodyne-102010'
        earlier = datetime(2018, 4, 3, 9, 51, 22)
        later = datetime(2018, 4, 3, 10, 51, 22)

        # End time specified - start defaults to one hour back
        reader = Reader(end_time=later)
        self.assertEqual(reader.end_time, later)
        self.assertEqual(reader.start_time, earlier)

        # Start time specified - end defaults to "now"
        reader = Reader(start_time=earlier)
        self.assertIsNotNone(reader.end_time)
        self.assertNotEqual(reader.end_time, later)
        self.assertEqual(reader.start_time, earlier)

    def test_iteration(self, MockLoggingClient):
        MockLoggingClient.return_value.project = 'yoyodyne-102010'
        MockLoggingClient.return_value.list_entries.return_value = MockIterator()

        earlier = datetime(2018, 4, 3, 9, 51, 22)
        later = datetime(2018, 4, 3, 10, 51, 33)
        reader = Reader(start_time=earlier, end_time=later, log_name='my_log')

        # Test for flows getting created
        actual = list(reader)
        expected = [FlowRecord(x) for x in SAMPLE_ENTRIES]
        self.assertEqual(actual, expected)

        # Test the client getting called correctly
        expression = (
            'resource.type="gce_subnetwork" AND '
            '(logName="my_log") AND '
            'Timestamp >= "2018-04-03T09:50:22Z" AND '
            'Timestamp < "2018-04-03T10:52:33Z" AND '
            'jsonPayload.start_time >= "2018-04-03T09:51:22Z" AND '
            'jsonPayload.start_time < "2018-04-03T10:51:33Z"'
        )
        MockLoggingClient.return_value.list_entries.assert_called_once_with(
            filter_=expression,
            page_size=1000,
            projects=['yoyodyne-102010'],
            page_token=None,
        )

    @patch(PREFIX('ResourceManagerClient'), autospec=True)
    @patch(PREFIX('Credentials'), autospec=True)
    def test_multiple_projects(
        self, MockCredentials, MockResourceManagerClient, MockLoggingClient
    ):
        creds = MagicMock(Credentials, project_id='proj1')
        MockCredentials.from_service_account_info.return_value = creds

        MockLoggingClient.return_value.project = 'yoyodyne-102010'
        proj1_iterator = MockIterator()
        proj1_iterator.pages = [[SAMPLE_ENTRIES[0]]]
        proj2_iterator = MockIterator()
        proj2_iterator.pages = [[SAMPLE_ENTRIES[1]]]
        proj3_iterator = MockIterator()
        proj3_iterator.pages = [[SAMPLE_ENTRIES[2], SAMPLE_ENTRIES[3]]]
        MockLoggingClient.return_value.list_entries.side_effect = [
            proj1_iterator,
            proj2_iterator,
            proj3_iterator,
        ]

        MockResourceManagerClient.return_value.list_projects.return_value = [
            MagicMock(project_id='proj1'),
            MagicMock(project_id='proj2'),
            MagicMock(project_id='proj3'),
        ]

        reader = Reader(
            start_time=datetime(2018, 4, 3, 9, 51, 22),
            end_time=datetime(2018, 4, 3, 10, 51, 33),
            service_account_info={'foo': 1},
            collect_multiple_projects=True,
        )

        MockCredentials.from_service_account_info.assert_called_once_with({'foo': 1})
        MockLoggingClient.assert_called_once_with(project='proj1', credentials=creds)

        # Test for flows getting created
        actual = list(reader)
        expected = [FlowRecord(x) for x in SAMPLE_ENTRIES]
        self.assertEqual(actual, expected)
        self.assertEqual(reader.bytes_processed, 576)

        # Test the client getting called correctly with multiple projects
        expression = (
            'resource.type="gce_subnetwork" AND '
            '(logName="projects/proj1/logs/'
            'compute.googleapis.com%2Fvpc_flows" OR '
            'logName="projects/proj2/logs/'
            'compute.googleapis.com%2Fvpc_flows" OR '
            'logName="projects/proj3/logs/'
            'compute.googleapis.com%2Fvpc_flows") AND '
            'Timestamp >= "2018-04-03T09:50:22Z" AND '
            'Timestamp < "2018-04-03T10:52:33Z" AND '
            'jsonPayload.start_time >= "2018-04-03T09:51:22Z" AND '
            'jsonPayload.start_time < "2018-04-03T10:51:33Z"'
        )
        mock_list_calls = MockLoggingClient.return_value.list_entries.mock_calls
        for proj in ('proj1', 'proj2', 'proj3'):
            self.assertIn(
                call(
                    filter_=expression, page_size=1000, projects=[proj], page_token=None
                ),
                mock_list_calls,
            )

    @patch(PREFIX('ResourceManagerClient'), autospec=True)
    def test_no_resource_manager_api(
        self, MockResourceManagerClient, MockLoggingClient
    ):
        MockResourceManagerClient.return_value.list_projects.side_effect = [
            GoogleAPIError,
        ]
        MockLoggingClient.return_value.project = 'yoyodyne-102010'
        MockLoggingClient.return_value.list_entries.return_value = MockIterator()
        self.assertEqual(
            Reader(
                start_time=datetime(2018, 4, 3, 9, 51, 22),
                end_time=datetime(2018, 4, 3, 10, 51, 33),
                collect_multiple_projects=True,
            ).log_list,
            [BASE_LOG_NAME.format('yoyodyne-102010')],
        )

    @patch(PREFIX('ResourceManagerClient'), autospec=True)
    def test_limited_project_access(self, MockResourceManagerClient, MockLoggingClient):
        MockResourceManagerClient.return_value.list_projects.return_value = [
            MagicMock(project_id='proj1'),
            MagicMock(project_id='proj2'),
            MagicMock(project_id='proj3'),
        ]
        MockLoggingClient.return_value.project = 'proj1'
        MockLoggingClient.return_value.list_entries.side_effect = [
            MockFailedIterator(),
            MockIterator(),
            MockNotFoundIterator(),
        ]
        reader = Reader(
            start_time=datetime(2018, 4, 3, 9, 51, 22),
            end_time=datetime(2018, 4, 3, 10, 51, 33),
            collect_multiple_projects=True,
        )
        self.assertEqual(
            reader.log_list,
            [
                BASE_LOG_NAME.format('proj1'),
                BASE_LOG_NAME.format('proj2'),
                BASE_LOG_NAME.format('proj3'),
            ],
        )
        self.assertEqual(list(reader), [FlowRecord(x) for x in SAMPLE_ENTRIES])

    @patch(PREFIX('ResourceManagerClient'), autospec=True)
    @patch(PREFIX('Credentials'), autospec=True)
    def test_log_list(
        self, MockCredentials, MockResourceManagerClient, MockLoggingClient
    ):
        MockLoggingClient.return_value.project = 'yoyodyne-102010'
        MockLoggingClient.return_value.list_entries.return_value = MockIterator()

        MockResourceManagerClient.return_value.list_projects.return_value = [
            MagicMock(project_id='yoyodyne-102010'),
            MagicMock(project_id='proj2'),
        ]

        # explicit log overwrites project_list
        self.assertEqual(
            Reader(
                start_time=datetime(2018, 4, 3, 9, 51, 22),
                end_time=datetime(2018, 4, 3, 10, 51, 33),
                log_name='my_log',
                collect_multiple_projects=True,
            ).log_list,
            ['my_log'],
        )

        # project_list includes multiple logs
        self.assertEqual(
            Reader(
                start_time=datetime(2018, 4, 3, 9, 51, 22),
                end_time=datetime(2018, 4, 3, 10, 51, 33),
                service_account_info={'foo': 1},
                collect_multiple_projects=True,
            ).log_list,
            [
                'projects/yoyodyne-102010/logs/compute.googleapis.com%2Fvpc_flows',
                'projects/proj2/logs/compute.googleapis.com%2Fvpc_flows',
            ],
        )

        # no project_list uses client list
        self.assertEqual(
            Reader(
                start_time=datetime(2018, 4, 3, 9, 51, 22),
                end_time=datetime(2018, 4, 3, 10, 51, 33),
                service_account_info={'foo': 1},
                collect_multiple_projects=False,
            ).log_list,
            ['projects/yoyodyne-102010/logs/compute.googleapis.com%2Fvpc_flows'],
        )


class AggregationTests(TestCase):
    def test_basic(self):
        flow_1 = FlowRecord(SAMPLE_ENTRIES[1])
        flow_1.start_time -= timedelta(days=1)

        flow_2 = FlowRecord(SAMPLE_ENTRIES[1])
        flow_2.end_time += timedelta(days=1)

        self.assertCountEqual(
            list(aggregated_records([flow_1, flow_2])),
            [
                (
                    ip_address('192.0.2.2'),
                    ip_address('198.51.100.75'),
                    3389,
                    49444,
                    6,
                    12,  # Packets doubled
                    1512,  # Bytes doubled
                    datetime(2018, 4, 2, 13, 47, 32),  # Earliest start
                    datetime(2018, 4, 4, 13, 47, 33),  # Latest finish
                ),
            ],
        )

    def test_custom_key(self):
        self.assertCountEqual(
            list(
                aggregated_records(
                    [FlowRecord(x) for x in SAMPLE_ENTRIES],
                    ['src_port', 'protocol'],
                )
            ),
            [
                (
                    3389,
                    6,
                    26,
                    1776,
                    datetime(2018, 4, 3, 13, 47, 31),
                    datetime(2018, 4, 3, 13, 48, 33),
                ),
                (
                    49444,
                    6,
                    4,
                    491,
                    datetime(2018, 4, 3, 13, 47, 37),
                    datetime(2018, 4, 3, 13, 47, 38),
                ),
                (
                    0,
                    1,
                    20,
                    1020,
                    datetime(2018, 4, 3, 13, 48, 33),
                    datetime(2018, 4, 3, 13, 48, 33),
                ),
            ],
        )


class MainCLITests(TestCase):
    def setUp(self):
        patch_path = PREFIX('LoggingClient')
        with patch(patch_path, autospec=True) as MockLoggingClient:
            MockLoggingClient.return_value.project = 'yoyodyne-102010'
            MockLoggingClient.return_value.list_entries.return_value = MockIterator()
            self.reader = Reader()

    def test_action_print(self):
        with redirect_stdout(StringIO()) as output:
            cli_module.action_print(self.reader)
        self.assertEqual(
            output.getvalue(),
            'src_ip\tdest_ip\tsrc_port\tdest_port\tprotocol\t'
            'start_time\tend_time\tbytes_sent\tpackets_sent\n'
            '198.51.100.75\t192.0.2.2\t49444\t3389\t6\t2018-04-03T13:47:37\t'
            '2018-04-03T13:47:38\t491\t4\n'
            '192.0.2.2\t198.51.100.75\t3389\t49444\t6\t2018-04-03T13:47:32\t'
            '2018-04-03T13:47:33\t756\t6\n'
            '192.0.2.2\t192.0.2.3\t3389\t65535\t6\t2018-04-03T13:47:31\t'
            '2018-04-03T13:48:33\t1020\t20\n'
            '192.0.2.2\t192.0.2.3\t0\t0\t1\t2018-04-03T13:48:33\t'
            '2018-04-03T13:48:33\t1020\t20\n',
        )

    def test_action_print_limit(self):
        with redirect_stdout(StringIO()) as output:
            cli_module.action_print(self.reader, 1)
        self.assertEqual(
            output.getvalue(),
            'src_ip\tdest_ip\tsrc_port\tdest_port\tprotocol\t'
            'start_time\tend_time\tbytes_sent\tpackets_sent\n'
            '198.51.100.75\t192.0.2.2\t49444\t3389\t6\t2018-04-03T13:47:37\t'
            '2018-04-03T13:47:38\t491\t4\n',
        )

    def test_action_print_error(self):
        with self.assertRaises(RuntimeError):
            cli_module.action_print(self.reader, 1, 2)

    def test_action_ipset(self):
        with redirect_stdout(StringIO()) as output:
            cli_module.action_ipset(self.reader)
        self.assertEqual(output.getvalue(), '192.0.2.2\n192.0.2.3\n198.51.100.75\n')

    def test_action_findip(self):
        with redirect_stdout(StringIO()) as output:
            cli_module.action_findip(self.reader, '192.0.2.3')
        self.assertEqual(
            output.getvalue(),
            'src_ip\tdest_ip\tsrc_port\tdest_port\tprotocol\t'
            'start_time\tend_time\tbytes_sent\tpackets_sent\n'
            '192.0.2.2\t192.0.2.3\t3389\t65535\t6\t2018-04-03T13:47:31\t'
            '2018-04-03T13:48:33\t1020\t20\n'
            '192.0.2.2\t192.0.2.3\t0\t0\t1\t2018-04-03T13:48:33\t'
            '2018-04-03T13:48:33\t1020\t20\n',
        )

    def test_action_aggregate(self):
        with redirect_stdout(StringIO()) as output:
            cli_module.action_aggregate(self.reader)
        self.assertEqual(len(output.getvalue().splitlines()), 5)

    def test_main_error(self):
        with redirect_stderr(StringIO()) as output:
            cli_module.main(['frobulate'])
        self.assertEqual(
            output.getvalue(),
            'unknown action: frobulate\n'
            'known actions: print, ipset, findip, aggregate\n',
        )

    @patch(PREFIX('ResourceManagerClient'), autospec=True)
    def test_main(self, MockResourceManagerClient):
        with patch(PREFIX('LoggingClient'), autospec=TestClient) as MockLoggingClient:
            MockLoggingClient.return_value.project = 'yoyodyne-102010'
            MockLoggingClient.return_value.list_entries.return_value = MockIterator()
            with redirect_stdout(StringIO()) as output:
                cli_module.main(
                    [
                        '--start-time',
                        '2018-04-03 12:00:00',
                        '--end-time',
                        '2018-04-03 13:00:00',
                        '--filters',
                        'jsonPayload.src_ip="198.51.100.1"',
                    ],
                )
        self.assertEqual(len(output.getvalue().splitlines()), 5)
        self.assertFalse(MockResourceManagerClient.called)

    @patch(PREFIX('ResourceManagerClient'), autospec=True)
    @patch(PREFIX('LoggingClient'), autospec=TestClient)
    def test_main_multi_project_argument(
        self, MockLoggingClient, MockResourceManagerClient
    ):
        MockLoggingClient.return_value.project = 'yoyodyne-102010'
        MockLoggingClient.return_value.list_entries.return_value = MockIterator()
        MockResourceManagerClient.return_value.list_projects.return_value = [
            MagicMock(project_id='yoyodyne-102010')
        ]
        with redirect_stdout(StringIO()) as output:
            cli_module.main(
                [
                    '--start-time',
                    '2018-04-03 12:00:00',
                    '--end-time',
                    '2018-04-03 13:00:00',
                    '--filters',
                    'jsonPayload.src_ip="198.51.100.1"',
                    '--collect-multiple-projects',
                ],
            )
        self.assertEqual(len(output.getvalue().splitlines()), 5)
        self.assertIn(call().list_projects(), MockResourceManagerClient.mock_calls)
