from datetime import datetime, timedelta
from ipaddress import ip_address
from io import StringIO
from unittest import TestCase
from unittest.mock import MagicMock, patch, call
from tempfile import NamedTemporaryFile

from gcp_flowlogs_reader.gcp_flowlogs_reader import BASE_LOG_NAME
from google.api_core.exceptions import (
    GoogleAPIError,
    PermissionDenied,
    NotFound,
)
from google.cloud.logging import Client
from google.cloud.logging.entries import StructEntry
from google.oauth2.service_account import Credentials

from gcp_flowlogs_reader.aggregation import aggregated_records
import gcp_flowlogs_reader.__main__ as cli_module
from gcp_flowlogs_reader import (
    FlowRecord,
    Reader,
    InstanceDetails,
    VpcDetails,
    GeographicDetails,
)

SAMPLE_PAYLODS = [
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
]

SAMPLE_ENTRIES = [StructEntry(x, None) for x in SAMPLE_PAYLODS]


class MockIterator:
    def __init__(self):
        self.pages = (
            [SAMPLE_ENTRIES[0], SAMPLE_ENTRIES[1]],
            [SAMPLE_ENTRIES[2]],
        )

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
                InstanceDetails(**SAMPLE_PAYLODS[0]['dest_instance']),
            ),
            ('src_vpc', None),
            ('dest_vpc', VpcDetails(**SAMPLE_PAYLODS[0]['dest_vpc'])),
            (
                'src_location',
                GeographicDetails(**SAMPLE_PAYLODS[0]['src_location']),
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
                InstanceDetails(**SAMPLE_PAYLODS[1]['src_instance']),
            ),
            ('dest_instance', None),
            ('src_vpc', VpcDetails(**SAMPLE_PAYLODS[1]['src_vpc'])),
            ('dest_vpc', None),
            ('src_location', None),
            (
                'dest_location',
                GeographicDetails(**SAMPLE_PAYLODS[1]['dest_location']),
            ),
        ]:
            with self.subTest(attr=attr):
                actual = getattr(flow_record, attr)
                self.assertEqual(actual, expected)
                self.assertEqual(type(actual), type(expected))

    def test_eq(self):
        self.assertEqual(
            FlowRecord(SAMPLE_ENTRIES[0]), FlowRecord(SAMPLE_ENTRIES[0])
        )
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
            ('dest_instance', SAMPLE_PAYLODS[0]['dest_instance']),
            ('src_vpc', None),
            ('dest_vpc', SAMPLE_PAYLODS[0]['dest_vpc']),
            ('src_location', SAMPLE_PAYLODS[0]['src_location']),
            ('dest_location', None),
        ]:
            with self.subTest(attr=attr):
                actual = flow_dict[attr]
                self.assertEqual(actual, expected)

    def test_from_payload(self):
        self.assertEqual(
            FlowRecord.from_payload(SAMPLE_PAYLODS[0]),
            FlowRecord(SAMPLE_ENTRIES[0]),
        )


@patch(
    'gcp_flowlogs_reader.gcp_flowlogs_reader.gcp_logging.Client',
    autospec=TestClient,
)
class ReaderTests(TestCase):
    def test_init_with_client(self, mock_Client):
        logging_client = MagicMock(Client)
        logging_client.project = 'yoyodyne-102010'
        reader = Reader(logging_client=logging_client)
        self.assertEqual(mock_Client.call_count, 0)
        self.assertIs(reader.logging_client, logging_client)

    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.Credentials', autospec=True
    )
    def test_init_with_credentials_info(self, mock_Credentials, mock_Client):
        creds = MagicMock(Credentials)
        creds.project_id = 'proj1'
        mock_Credentials.from_service_account_info.return_value = creds

        client = MagicMock(Client)
        client.project = 'yoyodyne-102010'
        mock_Client.return_value = client

        Reader(service_account_info={'foo': 1})

        mock_Credentials.from_service_account_info.assert_called_once_with(
            {'foo': 1}
        )
        mock_Client.assert_called_once_with(project='proj1', credentials=creds)

    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.Credentials', autospec=True
    )
    def test_init_with_credentials_info_and_project(
        self, mock_Credentials, mock_Client
    ):
        # The credentials file specifies one project_id
        creds = MagicMock(Credentials)
        creds.project_id = 'proj1'
        mock_Credentials.from_service_account_info.return_value = creds

        # The client has another one, which will be ignored
        client = MagicMock(Client)
        client.project = 'proj2'
        mock_Client.return_value = client

        # The request is for a third one, which we'll use
        Reader(service_account_info={'foo': 1}, project='proj3')

        mock_Credentials.from_service_account_info.assert_called_once_with(
            {'foo': 1}
        )
        mock_Client.assert_called_once_with(project='proj3', credentials=creds)

    def test_init_with_credentials_json(self, mock_Client):
        with NamedTemporaryFile() as temp_file:
            path = temp_file.name
            Reader(service_account_json=path, project='yoyodyne-102010')

        mock_Client.from_service_account_json.assert_called_once_with(
            path, project='yoyodyne-102010'
        )

    def test_init_with_environment(self, mock_Client):
        mock_Client.return_value.project = 'yoyodyne-102010'
        Reader(project='yoyodyne-102010')
        mock_Client.assert_called_once_with(project='yoyodyne-102010')

    def test_init_log_list(self, mock_Client):
        mock_Client.return_value.project = 'yoyodyne-1020'

        # Nothing specified - log name is derived from the project name
        normal_reader = Reader()
        self.assertEqual(
            normal_reader.log_list,
            ['projects/yoyodyne-1020/logs/compute.googleapis.com%2Fvpc_flows'],
        )

        # Custom name specified - log name is added to log list
        custom_reader = Reader(log_name='custom-log')
        self.assertEqual(custom_reader.log_list, ['custom-log'])

    def test_init_times(self, mock_Client):
        mock_Client.return_value.project = 'yoyodyne-102010'
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

    def test_iteration(self, mock_Client):
        mock_Client.return_value.project = 'yoyodyne-102010'
        mock_Client.return_value.list_entries.return_value = MockIterator()

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
        mock_Client.return_value.list_entries.assert_called_once_with(
            filter_=expression, page_size=1000, projects=['yoyodyne-102010']
        )

    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.resource_manager',
        autospec=True,
    )
    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.Credentials', autospec=True
    )
    def test_multiple_projects(
        self, mock_Credentials, mock_Resource_Manager, mock_Client
    ):
        creds = MagicMock(Credentials)
        creds.project_id = 'proj1'
        mock_Credentials.from_service_account_info.return_value = creds

        log_client = MagicMock(TestClient)
        log_client.project = 'yoyodyne-102010'
        proj1_iterator = MockIterator()
        proj1_iterator.pages = [[SAMPLE_ENTRIES[0]]]
        proj2_iterator = MockIterator()
        proj2_iterator.pages = [[SAMPLE_ENTRIES[1]]]
        proj3_iterator = MockIterator()
        proj3_iterator.pages = [[SAMPLE_ENTRIES[2]]]
        log_client.list_entries.side_effect = [
            proj1_iterator,
            proj2_iterator,
            proj3_iterator,
        ]
        mock_Client.return_value = log_client

        earlier = datetime(2018, 4, 3, 9, 51, 22)
        later = datetime(2018, 4, 3, 10, 51, 33)

        resource_client = MagicMock()
        mock_project1 = MagicMock(project_id='proj1')
        mock_project2 = MagicMock(project_id='proj2')
        mock_project3 = MagicMock(project_id='proj3')
        resource_client.list_projects.return_value = [
            mock_project1,
            mock_project2,
            mock_project3,
        ]
        project_list = ['proj1', 'proj2', 'proj3']
        mock_Resource_Manager.Client.return_value = resource_client

        reader = Reader(
            start_time=earlier,
            end_time=later,
            service_account_info={'foo': 1},
            collect_multiple_projects=True,
        )

        mock_Credentials.from_service_account_info.assert_called_once_with(
            {'foo': 1}
        )
        mock_Client.assert_called_once_with(project='proj1', credentials=creds)

        # Test for flows getting created
        actual = list(reader)
        expected = [FlowRecord(x) for x in SAMPLE_ENTRIES]
        self.assertEqual(actual, expected)

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
        mock_list_calls = mock_Client.return_value.list_entries.mock_calls
        for proj in project_list:
            self.assertIn(
                call(filter_=expression, page_size=1000, projects=[proj]),
                mock_list_calls,
            )

    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.resource_manager',
        autospec=True,
    )
    def test_no_resource_manager_api(self, mock_Resource_Manager, mock_Client):
        resource_client = MagicMock()
        mock_Resource_Manager.Client.return_value = resource_client
        resource_client.list_projects.side_effect = [GoogleAPIError]
        log_client = MagicMock(TestClient)
        log_client.project = 'yoyodyne-102010'
        log_client.list_entries.return_value = MockIterator()
        mock_Client.return_value = log_client
        earlier = datetime(2018, 4, 3, 9, 51, 22)
        later = datetime(2018, 4, 3, 10, 51, 33)
        reader = Reader(
            start_time=earlier,
            end_time=later,
            collect_multiple_projects=True,
        )
        self.assertEqual(
            reader.log_list, [BASE_LOG_NAME.format('yoyodyne-102010')]
        )

    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.resource_manager',
        autospec=True,
    )
    def test_limited_project_access(self, mock_Resource_Manager, mock_Client):
        resource_client = MagicMock()
        mock_Resource_Manager.Client.return_value = resource_client
        resource_client.list_projects.return_value = [
            MagicMock(project_id='proj1'),
            MagicMock(project_id='proj2'),
            MagicMock(project_id='proj3'),
        ]
        log_client = MagicMock(TestClient)
        log_client.project = 'proj1'
        log_client.list_entries.side_effect = [
            MockFailedIterator(),
            MockIterator(),
            MockNotFoundIterator(),
        ]
        mock_Client.return_value = log_client
        earlier = datetime(2018, 4, 3, 9, 51, 22)
        later = datetime(2018, 4, 3, 10, 51, 33)
        reader = Reader(
            start_time=earlier,
            end_time=later,
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
        entry_list = list(reader)
        self.assertEqual(entry_list, [FlowRecord(x) for x in SAMPLE_ENTRIES])

    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.resource_manager',
        autospec=True,
    )
    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.Credentials', autospec=True
    )
    def test_log_list(
        self, mock_Credentials, mock_Resource_Manager, mock_Client
    ):
        creds = MagicMock(Credentials)
        creds.project_id = 'proj1'
        mock_Credentials.from_service_account_info.return_value = creds

        mock_Client.return_value.project = 'yoyodyne-102010'
        mock_Client.return_value.list_entries.return_value = MockIterator()

        resource_client = MagicMock()
        mock_project1 = MagicMock(project_id='yoyodyne-102010')
        mock_project2 = MagicMock(project_id='proj2')
        resource_client.list_projects.return_value = [
            mock_project1,
            mock_project2,
        ]
        mock_Resource_Manager.Client.return_value = resource_client

        earlier = datetime(2018, 4, 3, 9, 51, 22)
        later = datetime(2018, 4, 3, 10, 51, 33)
        reader = Reader(
            start_time=earlier,
            end_time=later,
            log_name='my_log',
            collect_multiple_projects=True,
        )
        # explicit log overwrites project_list
        self.assertEqual(reader.log_list, ['my_log'])
        reader = Reader(
            start_time=earlier,
            end_time=later,
            service_account_info={'foo': 1},
            collect_multiple_projects=True,
        )

        # project_list includes multiple logs
        log_string = 'projects/{}/logs/compute.googleapis.com%2Fvpc_flows'
        self.assertEqual(
            reader.log_list,
            [log_string.format('yoyodyne-102010'), log_string.format('proj2')],
        )

        # no project_list uses client list
        reader = Reader(
            start_time=earlier,
            end_time=later,
            service_account_info={'foo': 1},
            collect_multiple_projects=False,
        )
        self.assertEqual(
            reader.log_list, [log_string.format('yoyodyne-102010')]
        )


class AggregationTests(TestCase):
    def test_basic(self):
        flow_1 = FlowRecord(SAMPLE_ENTRIES[1])
        flow_1.start_time -= timedelta(days=1)

        flow_2 = FlowRecord(SAMPLE_ENTRIES[1])
        flow_2.end_time += timedelta(days=1)

        input_records = [flow_1, flow_2]
        output_records = list(aggregated_records(input_records))
        self.assertEqual(len(output_records), 1)
        actual = output_records[0]
        expected = (
            ip_address('192.0.2.2'),
            ip_address('198.51.100.75'),
            3389,
            49444,
            6,
            12,  # Packets doubled
            1512,  # Bytes doubled
            datetime(2018, 4, 2, 13, 47, 32),  # Earliest start
            datetime(2018, 4, 4, 13, 47, 33),  # Latest finish
        )
        self.assertEqual(actual, expected)

    def test_custom_key(self):
        input_records = [FlowRecord(x) for x in SAMPLE_ENTRIES]
        key_fields = ['src_port', 'protocol']
        output_records = sorted(
            aggregated_records(input_records, key_fields),
            key=lambda x: x.src_port,
        )
        self.assertEqual(len(output_records), 2)
        actual = output_records[0]
        expected = (
            3389,
            6,
            26,
            1776,
            datetime(2018, 4, 3, 13, 47, 31),
            datetime(2018, 4, 3, 13, 48, 33),
        )
        self.assertEqual(tuple(actual), expected)


class MainCLITests(TestCase):
    def setUp(self):
        patch_path = (
            'gcp_flowlogs_reader.gcp_flowlogs_reader.gcp_logging.Client'
        )
        with patch(patch_path, autospec=True) as mock_Client:
            mock_Client.return_value.project = 'yoyodyne-102010'
            mock_Client.return_value.list_entries.return_value = MockIterator()
            self.reader = Reader()

    def test_action_print(self):
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            cli_module.action_print(self.reader)
            actual = mock_stdout.getvalue()
        expected = (
            'src_ip\tdest_ip\tsrc_port\tdest_port\tprotocol\t'
            'start_time\tend_time\tbytes_sent\tpackets_sent\n'
            '198.51.100.75\t192.0.2.2\t49444\t3389\t6\t2018-04-03T13:47:37\t'
            '2018-04-03T13:47:38\t491\t4\n'
            '192.0.2.2\t198.51.100.75\t3389\t49444\t6\t2018-04-03T13:47:32\t'
            '2018-04-03T13:47:33\t756\t6\n'
            '192.0.2.2\t192.0.2.3\t3389\t65535\t6\t2018-04-03T13:47:31\t'
            '2018-04-03T13:48:33\t1020\t20\n'
        )
        self.assertEqual(actual, expected)

    def test_action_print_limit(self):
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            cli_module.action_print(self.reader, 1)
            actual = mock_stdout.getvalue()
        expected = (
            'src_ip\tdest_ip\tsrc_port\tdest_port\tprotocol\t'
            'start_time\tend_time\tbytes_sent\tpackets_sent\n'
            '198.51.100.75\t192.0.2.2\t49444\t3389\t6\t2018-04-03T13:47:37\t'
            '2018-04-03T13:47:38\t491\t4\n'
        )
        self.assertEqual(actual, expected)

    def test_action_print_error(self):
        with self.assertRaises(RuntimeError):
            cli_module.action_print(self.reader, 1, 2)

    def test_action_ipset(self):
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            cli_module.action_ipset(self.reader)
            actual = mock_stdout.getvalue()
        expected = '192.0.2.2\n' '192.0.2.3\n' '198.51.100.75\n'
        self.assertEqual(actual, expected)

    def test_action_findip(self):
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            cli_module.action_findip(self.reader, '192.0.2.3')
            actual = mock_stdout.getvalue()
        expected = (
            'src_ip\tdest_ip\tsrc_port\tdest_port\tprotocol\t'
            'start_time\tend_time\tbytes_sent\tpackets_sent\n'
            '192.0.2.2\t192.0.2.3\t3389\t65535\t6\t2018-04-03T13:47:31\t'
            '2018-04-03T13:48:33\t1020\t20\n'
        )
        self.assertEqual(actual, expected)

    def test_action_aggregate(self):
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            cli_module.action_aggregate(self.reader)
            actual_len = len(mock_stdout.getvalue().splitlines())
        expected_len = 4
        self.assertEqual(actual_len, expected_len)  # TODO: more thorough test

    def test_main_error(self):
        with patch('sys.stderr', new_callable=StringIO) as mock_stderr:
            cli_module.main(['frobulate'])
            actual_len = len(mock_stderr.getvalue().splitlines())
        expected_len = 2
        self.assertEqual(actual_len, expected_len)  # TODO: more thorough test

    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.resource_manager',
        autospec=True,
    )
    def test_main(self, mock_Resource_Manager):
        patch_path = (
            'gcp_flowlogs_reader.gcp_flowlogs_reader.gcp_logging.Client'
        )
        with patch(patch_path, autospec=TestClient) as mock_Client:
            mock_Client.return_value.project = 'yoyodyne-102010'
            mock_Client.return_value.list_entries.return_value = MockIterator()

            argv = [
                '--start-time',
                '2018-04-03 12:00:00',
                '--end-time',
                '2018-04-03 13:00:00',
                '--filters',
                'jsonPayload.src_ip="198.51.100.1"',
            ]
            with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
                cli_module.main(argv)
                actual_len = len(mock_stdout.getvalue().splitlines())
        expected_len = 4
        self.assertEqual(actual_len, expected_len)  # TODO: more thorough test
        self.assertFalse(mock_Resource_Manager.Client.called)

    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.resource_manager',
        autospec=True,
    )
    @patch(
        'gcp_flowlogs_reader.gcp_flowlogs_reader.gcp_logging.Client',
        autospec=TestClient,
    )
    def test_main_multi_project_argument(
        self, mock_Client, mock_Resource_Manager
    ):
        mock_Client.return_value.project = 'yoyodyne-102010'
        mock_Client.return_value.list_entries.return_value = MockIterator()
        resource_client = MagicMock()
        mock_project1 = MagicMock(project_id='yoyodyne-102010')
        resource_client.list_projects.return_value = [mock_project1]
        mock_Resource_Manager.Client.return_value = resource_client

        argv = [
            '--start-time',
            '2018-04-03 12:00:00',
            '--end-time',
            '2018-04-03 13:00:00',
            '--filters',
            'jsonPayload.src_ip="198.51.100.1"',
            '--collect-multiple-projects',
        ]
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            cli_module.main(argv)
            actual_len = len(mock_stdout.getvalue().splitlines())
        expected_len = 4
        self.assertEqual(actual_len, expected_len)
        self.assertIn(
            call().list_projects(), mock_Resource_Manager.Client.mock_calls
        )
