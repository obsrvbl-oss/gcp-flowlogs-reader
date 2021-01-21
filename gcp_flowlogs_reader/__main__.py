import sys
from argparse import ArgumentParser
from datetime import datetime

from .gcp_flowlogs_reader import Reader
from .aggregation import aggregated_records

actions = {}


def print_header():
    print(
        'src_ip',
        'dest_ip',
        'src_port',
        'dest_port',
        'protocol',
        'start_time',
        'end_time',
        'bytes_sent',
        'packets_sent',
        sep='\t',
    )


def print_record(record):
    print(
        record.src_ip,
        record.dest_ip,
        record.src_port,
        record.dest_port,
        record.protocol,
        record.start_time.strftime('%Y-%m-%dT%H:%M:%S'),
        record.end_time.strftime('%Y-%m-%dT%H:%M:%S'),
        record.bytes_sent,
        record.packets_sent,
        sep='\t',
    )


def action_print(reader, *args):
    """Simply print the Flow Log records to output."""
    arg_count = len(args)
    if arg_count == 0:
        stop_after = 0
    elif arg_count == 1:
        stop_after = int(args[0])
    else:
        raise RuntimeError("0 or 1 arguments expected for action 'print'")

    print_header()
    for i, record in enumerate(reader, 1):
        print_record(record)
        if i == stop_after:
            break


actions['print'] = action_print


def action_ipset(reader, *args):
    """Show the set of IPs seen in Flow Log records."""
    ip_set = set()
    for record in reader:
        ip_set.add(record.src_ip)
        ip_set.add(record.dest_ip)

    for ip in sorted(ip_set):
        print(ip)


actions['ipset'] = action_ipset


def action_findip(reader, *args):
    """Find Flow Log records involving a specific IP or IPs."""
    target_ips = set(args)
    print_header()
    for record in reader:
        if (str(record.src_ip) in target_ips) or (str(record.dest_ip) in target_ips):
            print_record(record)


actions['findip'] = action_findip


def action_aggregate(reader, *args):
    """Aggregate flow records by 5-tuple and print a tab-separated stream"""
    all_aggregated = aggregated_records(reader)
    print_header()

    # Join the first row with the rest of the rows and print them
    for record in all_aggregated:
        print_record(record)


actions['aggregate'] = action_aggregate


def get_reader(args):
    kwargs = {}

    time_format = args.time_format
    if args.start_time:
        kwargs['start_time'] = datetime.strptime(args.start_time, time_format)
    if args.end_time:
        kwargs['end_time'] = datetime.strptime(args.end_time, time_format)

    if args.filters:
        kwargs['filters'] = args.filters.split(' AND ')

    kwargs['collect_multiple_projects'] = args.collect_multiple_projects

    kwargs['service_account_json'] = args.credentials_file
    kwargs['log_name'] = args.log_name

    return Reader(**kwargs)


def main(argv=None):
    argv = argv or sys.argv[1:]
    parser = ArgumentParser(description='Read records from Google Cloud VPC Flow Logs')
    parser.add_argument(
        'action',
        nargs='*',
        default=['print'],
        help='action to take on log records',
    )
    parser.add_argument(
        '-s',
        '--start-time',
        metavar='WHEN',
        help='filter for records at or after this time (default: one hour ago)',
    )
    parser.add_argument(
        '-e',
        '--end-time',
        metavar='WHEN',
        help='filter stream records before this time (default: now)',
    )
    parser.add_argument(
        '--time-format',
        default='%Y-%m-%d %H:%M:%S',
        metavar='FORMAT',
        help=(
            'interpret --start-time and --end-time using this strftime(3) format '
            '(default: "%(default)s")'
        ),
    )
    parser.add_argument(
        '--filters',
        help='additional filters to be applied server-side',
    )
    parser.add_argument(
        '--credentials-file',
        metavar='FILE',
        help=(
            'path to a JSON file with service account credentials '
            '(default: use the GOOGLE_APPLICATION_CREDENTIALS environment variable)'
        ),
    )
    parser.add_argument(
        '--collect-multiple-projects',
        action='store_true',
        help='collect flows from multiple projects',
    )
    parser.add_argument(
        '--log-name',
        metavar='NAME',
        help='name of the StackDriver log name to read (default: use the project name)',
    )
    args = parser.parse_args(argv)

    # Confirm the specified action is valid
    action = args.action[0]
    try:
        action_method = actions[action]
    except KeyError:
        print(f'unknown action: {action}', file=sys.stderr)
        print('known actions: {}'.format(', '.join(actions)), file=sys.stderr)
        return

    reader = get_reader(args)
    action_method(reader, *args.action[1:])


if __name__ == '__main__':
    main()
