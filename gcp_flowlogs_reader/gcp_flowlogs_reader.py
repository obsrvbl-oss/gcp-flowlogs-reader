from datetime import datetime, timedelta
from ipaddress import ip_address, IPv4Address, IPv6Address
from time import sleep
from typing import NamedTuple, Optional, Union

from google.api_core.exceptions import (
    Forbidden,
    GoogleAPIError,
    NotFound,
    TooManyRequests,
)
from google.cloud.logging import Client as LoggingClient
from google.cloud.logging.entries import StructEntry

try:
    from google.cloud.resource_manager import Client as ResourceManagerClient
except ImportError:
    from google.cloud.resourcemanager import ProjectsClient as ResourceManagerClient
from google.oauth2.service_account import Credentials

BASE_LOG_NAME = 'projects/{}/logs/compute.googleapis.com%2Fvpc_flows'


def page_helper(logging_client, wait_time=1.0, **kwargs):
    kwargs['page_token'] = None
    while True:
        try:
            iterator = logging_client.list_entries(**kwargs)
            for page in iterator.pages:
                kwargs['page_token'] = iterator.next_page_token
                yield from page
            break
        except TooManyRequests:
            sleep(wait_time)
            pass


class InstanceDetails(NamedTuple):
    project_id: str
    vm_name: str
    region: str
    zone: str


class VpcDetails(NamedTuple):
    project_id: str
    vpc_name: str
    subnetwork_name: str


class GeographicDetails(NamedTuple):
    continent: str
    country: str
    region: str
    city: str


class ResourceLabels(NamedTuple):
    project_id: str
    location: str
    subnetwork_id: str
    subnetwork_name: str


def safe_tuple_from_dict(cls, attrs):
    attr_payload = {k: attrs[k] for k in cls._fields}
    return cls(**attr_payload)


class FlowRecord:
    src_ip: Union[IPv4Address, IPv6Address]
    src_port: int
    dest_ip: Union[IPv4Address, IPv6Address]
    dest_port: int
    protocol: int
    start_time: datetime
    end_time: datetime
    bytes_sent: int
    packets_sent: int
    rtt_msec: Optional[int]
    reporter: str
    src_instance: Optional[InstanceDetails]
    dest_instance: Optional[InstanceDetails]
    src_vpc: Optional[VpcDetails]
    dest_vpc: Optional[VpcDetails]
    src_location: Optional[GeographicDetails]
    dest_location: Optional[GeographicDetails]
    resource_labels: Optional[ResourceLabels]

    __slots__ = list(__annotations__)

    def __init__(self, entry: StructEntry):
        flow_payload = entry.payload or entry.log_name
        connection = flow_payload['connection']
        self.src_ip = ip_address(connection['src_ip'])
        self.src_port = int(connection.get('src_port', 0))
        self.dest_ip = ip_address(connection['dest_ip'])
        self.dest_port = int(connection.get('dest_port', 0))
        self.protocol = int(connection['protocol'])

        self.end_time = self._get_dt(flow_payload['end_time'])
        self.start_time = self._get_dt(
            flow_payload.get('start_time', flow_payload['end_time'])
        )

        self.bytes_sent = int(flow_payload['bytes_sent'])
        self.packets_sent = int(flow_payload['packets_sent'])

        rtt_msec = flow_payload.get('rtt_msec')
        self.rtt_msec = None if rtt_msec is None else int(rtt_msec)

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
                value = safe_tuple_from_dict(cls, flow_payload[attr])
            except (KeyError, TypeError):
                setattr(self, attr, None)
            else:
                setattr(self, attr, value)

        try:
            self.resource_labels = ResourceLabels(**entry.resource.labels)
        except (AttributeError, TypeError):
            self.resource_labels = None

    @staticmethod
    def _get_dt(value):
        return datetime.strptime(value[:19], '%Y-%m-%dT%H:%M:%S')

    def __eq__(self, other):
        try:
            return all(getattr(self, x) == getattr(other, x) for x in self.__slots__)
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
        return ', '.join(f'{x}: {getattr(self, x)}' for x in self.__slots__[:9])

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
        return cls(StructEntry(payload, None))


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
        wait_time=1.0,
        **kwargs,
    ):
        # If a Client instance is provided, use it.
        if logging_client:
            self.logging_client = logging_client
        # If a service account JSON file was provided, try it.
        elif service_account_json:
            self.logging_client = LoggingClient.from_service_account_json(
                service_account_json, **kwargs
            )
        elif service_account_info:
            gcp_credentials = Credentials.from_service_account_info(
                service_account_info
            )
            # use the project specified in the credentials
            client_args = {'project': gcp_credentials.project_id}
            client_args.update(kwargs)

            self.logging_client = LoggingClient(
                credentials=gcp_credentials, **client_args
            )
        # Failing that, use the GOOGLE_APPLICATION_CREDENTIALS environment variable.
        else:
            self.logging_client = LoggingClient(**kwargs)

        # capture project list, each project requires log view permissions
        if collect_multiple_projects:
            self.project_list = self._get_project_list(self.logging_client)
        else:
            self.project_list = [self.logging_client.project]

        # The default list of logs is based on the project name and project list, but
        # it can be overridden by providing it explicitly.
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
        self.wait_time = wait_time
        self.filters = filters or []
        self.iterator = self._reader()
        self.bytes_processed = 0

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.iterator)

    @staticmethod
    def _format_dt(dt):
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    @staticmethod
    def _get_project_list(log_client):
        try:
            client = ResourceManagerClient(credentials=log_client._credentials)
            project_list = [x.project_id for x in client.list_projects()]
        except GoogleAPIError:  # no permission to collect other projects
            return [log_client.project]
        return project_list

    def _reader(self):
        # When filtering by time, use the indexed Timestamp field for fast searches,
        # then filter for the payload timestamp.
        padding = timedelta(minutes=1)
        timestamp_start = self._format_dt(self.start_time - padding)
        timestamp_end = self._format_dt(self.end_time + padding)
        payload_start = self._format_dt(self.start_time)
        payload_end = self._format_dt(self.end_time)

        log_filters = [f'logName="{log_elm}"' for log_elm in self.log_list]
        full_log_filter = ' OR '.join(log_filters)

        filters = self.filters[:] + [
            'resource.type="gce_subnetwork"',
            f'({full_log_filter})',
            f'Timestamp >= "{timestamp_start}"',
            f'Timestamp < "{timestamp_end}"',
            f'jsonPayload.start_time >= "{payload_start}"',
            f'jsonPayload.start_time < "{payload_end}"',
        ]

        for project in self.project_list:
            try:
                for flow_entry in page_helper(
                    self.logging_client,
                    wait_time=self.wait_time,
                    filter_=' AND '.join(filters),
                    page_size=self.page_size,
                    projects=[project],
                ):
                    self.bytes_processed += flow_entry.__sizeof__()
                    yield FlowRecord(flow_entry)
            except (Forbidden, NotFound):  # Expected for removed/restricted projects
                pass
