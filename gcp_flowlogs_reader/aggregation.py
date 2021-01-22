from collections import defaultdict, namedtuple
from datetime import datetime

KEY_FIELDS = ['src_ip', 'dest_ip', 'src_port', 'dest_port', 'protocol']
VALUE_FIELDS = ['packets_sent', 'bytes_sent', 'start_time', 'end_time']


class _FlowStats:
    __slots__ = VALUE_FIELDS[:]

    def __init__(self):
        self.start_time = datetime.max
        self.end_time = datetime.min
        self.packets_sent = 0
        self.bytes_sent = 0

    def update(self, flow_record):
        if flow_record.start_time < self.start_time:
            self.start_time = flow_record.start_time
        if flow_record.end_time > self.end_time:
            self.end_time = flow_record.end_time
        self.packets_sent += flow_record.packets_sent
        self.bytes_sent += flow_record.bytes_sent

    def to_dict(self):
        return {x: getattr(self, x) for x in self.__slots__}


def aggregated_records(all_records, key_fields=KEY_FIELDS):
    StatRecord = namedtuple('StatRecord', key_fields + VALUE_FIELDS)

    flow_table = defaultdict(_FlowStats)
    for flow_record in all_records:
        key = tuple(getattr(flow_record, attr) for attr in key_fields)
        flow_table[key].update(flow_record)

    for key in flow_table:
        item = {k: v for k, v in zip(key_fields, key)}
        item.update(flow_table[key].to_dict())
        yield StatRecord(**item)
