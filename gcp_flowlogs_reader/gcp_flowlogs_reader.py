from collections import namedtuple
from datetime import datetime, timedelta
from ipaddress import ip_address

from google.api_core.exceptions import GoogleAPIError
from google.cloud import logging as gcp_logging, resource_manager
from google.oauth2.service_account import Credentials

BASE_LOG_NAME = 'projects/{}/logs/compute.googleapis.com%2Fvpc_flows'

InstanceDetails = namedtuple(
    'InstanceDetails', ['project_id', 'vm_name', 'region', 'zone']
)
VpcDetails = namedtuple(
    'VpcDetails', ['project_id', 'vpc_name', 'subnetwork_name']
)
GeographicDetails = namedtuple(
    'GeographicDetails', ['continent', 'country', 'region', 'city']
)


class FlowRecord:
    __slots__ = [
        'src_ip',
        'src_port',
        'dest_ip',
        'dest_port',
        'protocol',
        'start_time',
        'end_time',
        'bytes_sent',
        'packets_sent',
        'rtt_msec',
        'reporter',
        'src_instance',
        'dest_instance',
        'src_vpc',
        'dest_vpc',
        'src_location',
        'dest_location',
    ]

    def __init__(self, entry):
        flow_payload = entry.payload or entry.log_name
        connection = flow_payload['connection']
        self.src_ip = ip_address(connection['src_ip'])
        self.src_port = int(connection['src_port'])
        self.dest_ip = ip_address(connection['dest_ip'])
        self.dest_port = int(connection['dest_port'])
        self.protocol = int(connection['protocol'])

        self.start_time = self._get_dt(flow_payload['start_time'])
        self.end_time = self._get_dt(flow_payload['end_time'])

        self.bytes_sent = int(flow_payload['bytes_sent'])
        self.packets_sent = int(flow_payload['packets_sent'])

        rtt_msec = flow_payload.get('rtt_msec')
        self.rtt_msec = int(rtt_msec) if (rtt_msec is not None) else None

        self.reporter = flow_payload['reporter']

        for attr, cls in [
            ('src_instance', InstanceDetails),
            ('dest_instance', InstanceDetails),
            ('src_vpc', VpcDetails),
            ('dest_vpc', VpcDetails),
            ('src_location', GeographicDetails),
            ('dest_location', GeographicDetails),
        ]:
            try:
                value = cls(**flow_payload[attr])
            except (KeyError, TypeError):
                setattr(self, attr, None)
            else:
                setattr(self, attr, value)

    def _get_dt(self, value):
        return datetime.strptime(value[:19], '%Y-%m-%dT%H:%M:%S')

    def __eq__(self, other):
        try:
            return all(
                getattr(self, x) == getattr(other, x) for x in self.__slots__
            )
        except AttributeError:
            return False

    def __hash__(self):
        return hash(tuple(getattr(self, x) for x in self.__slots__))

    def __repr__(self):
        return '<FlowRecord {}:{}/{}->{}:{}/{}>'.format(
            self.src_ip,
            self.src_port,
            self.protocol,
            self.dest_ip,
            self.dest_port,
            self.protocol,
        )

    def __str__(self):
        str_slots = self.__slots__[:9]
        ret = ['{}: {}'.format(x, getattr(self, x)) for x in str_slots]
        return ', '.join(ret)

    def to_dict(self):
        nt_types = (InstanceDetails, VpcDetails, GeographicDetails)
        ret = {}
        for key in self.__slots__:
            value = getattr(self, key)
            if isinstance(value, nt_types):
                value = value._asdict()
            ret[key] = value

        return ret

    @classmethod
    def from_payload(cls, payload):
        entry = gcp_logging.entries.StructEntry(payload, None)
        return cls(entry)


class Reader:
    def __init__(
        self,
        log_name=None,
        start_time=None,
        end_time=None,
        filters=None,
        collect_multiple_projects=False,
        logging_client=None,
        service_account_json=None,
        service_account_info=None,
        page_size=1000,
        **kwargs,
    ):
        # If a Client instance is provided, use it.
        if logging_client:
            self.logging_client = logging_client
        # If a service account JSON file was provided, try it.
        elif service_account_json:
            self.logging_client = gcp_logging.Client.from_service_account_json(
                service_account_json, **kwargs
            )
        elif service_account_info:
            gcp_credentials = Credentials.from_service_account_info(
                service_account_info
            )
            # use the project specified in the credentials
            client_args = {'project': gcp_credentials.project_id}
            client_args.update(kwargs)

            self.logging_client = gcp_logging.Client(
                credentials=gcp_credentials, **client_args
            )
        # Failing that, use the GOOGLE_APPLICATION_CREDENTIALS environment
        # variable.
        else:
            self.logging_client = gcp_logging.Client(**kwargs)

        # capture project list, each project requires log view permissions
        if collect_multiple_projects:
            self.project_list = self._get_project_list(self.logging_client)
        else:
            self.project_list = [self.logging_client.project]

        # The default list of logs is based on the project name and
        # project list, but it can be overridden by providing it explicitly.
        if log_name:
            self.log_list = [log_name]
        else:
            self.log_list = [
                BASE_LOG_NAME.format(log_elm) for log_elm in self.project_list
            ]

        # If no time bounds are given, use the last hour.
        self.end_time = end_time or datetime.utcnow()
        self.start_time = start_time or (self.end_time - timedelta(hours=1))

        self.page_size = page_size
        self.filters = filters or []
        self.iterator = self._reader()

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.iterator)

    def _format_dt(self, dt):
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    def _get_project_list(self, log_client):
        credentials = log_client._credentials
        try:
            client = resource_manager.Client(credentials=credentials)
            project_list = [x.project_id for x in client.list_projects()]
        except GoogleAPIError:  # no permission to collect other projects
            return [log_client.project]
        return project_list

    def _reader(self):
        # When filtering by time, use the indexed Timestamp field for fast
        # searches, then filter for the payload timestamp.
        padding = timedelta(minutes=1)
        timestamp_start = self._format_dt(self.start_time - padding)
        timestamp_end = self._format_dt(self.end_time + padding)
        payload_start = self._format_dt(self.start_time)
        payload_end = self._format_dt(self.end_time)

        log_filters = [
            'logName="{}"'.format(log_elm) for log_elm in self.log_list
        ]
        full_log_filter = ' OR '.join(log_filters)

        filters = self.filters[:] + [
            'resource.type="gce_subnetwork"',
            '({})'.format(full_log_filter),
            'Timestamp >= "{}"'.format(timestamp_start),
            'Timestamp < "{}"'.format(timestamp_end),
            'jsonPayload.start_time >= "{}"'.format(payload_start),
            'jsonPayload.start_time < "{}"'.format(payload_end),
        ]
        expression = ' AND '.join(filters)

        for project in self.project_list:
            try:
                iterator = self.logging_client.list_entries(
                    filter_=expression,
                    page_size=self.page_size,
                    projects=[project],  # only collects current project flows
                )
                for page in iterator.pages:
                    for flow_entry in page:
                        yield FlowRecord(flow_entry)
            except GoogleAPIError:  # Expected for removed/restricted projects
                pass
