from .aggregation import aggregated_records
from .gcp_flowlogs_reader import (
    FlowRecord,
    Reader,
    InstanceDetails,
    VpcDetails,
    GeographicDetails,
    ResourceLabels,
)

__all__ = [
    'aggregated_records',
    'FlowRecord',
    'Reader',
    'InstanceDetails',
    'VpcDetails',
    'GeographicDetails',
    'ResourceLabels',
]
