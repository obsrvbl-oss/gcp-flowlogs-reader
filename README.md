# gcp-flowlogs-reader

This is `gcp-flowlogs-reader`, a command line tool and Python library for
retrieving and manipulating _VPC Flow Logs_ for the Google Cloud platform.

VPC Flow Logs record metadata about network communication inside your
Google Cloud VPC. They can be used for security monitoring and performance
analysis, and analogous to NetFlow and IPFIX records for on-premises networks.

For more information about VPC Flow Logs in Google Cloud, see
[these docs](https://cloud.google.com/vpc/docs/using-flow-logs).

Note that this branch wraps version 1 of the [Python Client for Cloud Logging](https://github.com/googleapis/python-logging).

## Installation and authentication

Clone this repository and run `python setup.py install` to install the
library.

Once it's installed, the `gcp_flowlogs_reader` tool should be available to run.
You may also invoke it with `python -m gcp_flowlogs_reader`.

```shell
$ gcp_flowlogs_reader -h
usage: gcp_flowlogs_reader [-h] [--start-time START_TIME]
                           [--end-time END_TIME] [--time-format TIME_FORMAT]
                           [--filters FILTERS]
                           [--credentials-file CREDENTIALS_FILE]
                           [--log-name LOG_NAME]
                           [action [action ...]]

```

If the `GOOGLE_APPLICATION_CREDENTIALS` environment variable is pointing to
a JSON file holding service account credentials, the tool will use that.
Otherwise, authenticate per
[Google instructions](https://google-cloud-python.readthedocs.io/en/latest/core/auth.html)
or set the `--credentials-file` switch.

### Printing flows

By default, the tool will print VPC Flow Log records from your project under
the default name. It will look back one hour:

```shell
$ gcp_flowlogs_reader --credentials-file="/home/service-account-.json"
src_ip	dest_ip	src_port	dest_port	protocol	start_time	end_time	bytes_sent	packets_sent
192.0.2.2	198.51.100.53	22	53658	6	2018-04-03T19:07:59	2018-04-03T19:08:00	3220	12
203.0.113.75	192.0.2.2	57772	22	6	2018-04-03T19:08:11	2018-04-03T19:08:12	5400	13
198.51.100.53	192.0.2.2	53658	22	6	2018-04-03T19:08:00	2018-04-03T19:08:00	2836	4
192.0.2.2	203.0.113.75	3389	57772	6	2018-04-03T19:08:11	2018-04-03T19:08:11	3384	8
```

You can also specify `gcp_flowlogs_reader print <limit>` to limit the number
printed.

### Time windows

To change the default one-hour lookback, set `--start-time` or `--end-time`.

The default format is `%Y-%m-%d %H:%M:%S` and assumes the UTC time zone.
You can change it with the `--time-format` switch.
See `strftime(3)` or the Python documentation for
[`strptime`](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-behavior)
for information on constructing format strings.


### Other actions

To print the set of internal and external IP addresses in the flow records,
use the `ipset` action:

```shell
$ gcp_flowlogs_reader --start-time="2018-04-03 14:00:00" ipset
192.0.2.2
192.0.2.3
198.51.100.53
203.0.113.75
```

To find all flows associated with a single IP address, use the `findip` action:

```shell
$ python -m gcp_flowlogs_reader findip 198.51.100.3
src_ip	dest_ip	src_port	dest_port	protocol	start_time	end_time	bytes_sent	packets_sent
198.51.100.3	192.0.2.3	34993	3389	6	2018-04-03T19:21:40	2018-04-03T19:21:40	0	9
198.51.100.3	192.0.2.3	34993	3389	6	2018-04-03T19:21:40	2018-04-03T19:21:40	459	11
198.51.100.3	192.0.2.3	60504	3389	6	2018-04-03T19:22:32	2018-04-03T19:22:32	258	6
192.0.2.3	198.51.100.3	3389	60504	6	2018-04-03T19:22:32	2018-04-03T19:22:34	3256	10
```

To aggregate flows with the same 5-tuple key (source address, source port,
destination address, destination port, protocol), use the `aggregate` action.


## Library usage

`gcp_flowlogs_reader.FlowRecord` transforms the JSON Payload from the
VPC Flow Logs entry into a Python object with standard types:

```
| Attribute     | Type                 | Example                                                                                              |
|---------------|----------------------|------------------------------------------------------------------------------------------------------|
| src_ip        | ipaddress.ip_address | ipaddress.ip_address('192.0.2.2')                                                                    |
| src_port      | int                  | 3389                                                                                                 |
| dest_ip       | ipaddress.ip_address | ipaddress.ip_address('198.51.100.1')                                                                 |
| dest_port     | int                  | 49152                                                                                                |
| protocol      | int                  | 6                                                                                                    |
| start_time    | datetime.datetime    | datetime.datetime(2018, 4, 4, 11, 55, 12, 943517)                                                    |
| end_time      | datetime.datetime    | datetime.datetime(2018, 4, 4, 11, 55, 14, 125304)                                                    |
| bytes_sent    | int                  | 4446                                                                                                 |
| packets_sent  | int                  | 13                                                                                                   |
| rtt_msec      | int                  | 233                                                                                                  |
| reporter      | str                  | 'SRC'                                                                                                |
| src_instance  | namedtuple           | InstanceDetails(project_id='yoyodyne-1020', vm_name='vm-1020', region='us-west1', zone='us-west1-a') |
| dest_instance | namedtuple           | InstanceDetails(project_id='yoyodyne-1020', vm_name='vm-1020', region='us-west1', zone='us-west1-a') |
| src_vpc       | namedtuple           | VpcDetails(project_id='yoyo-1020', vpc_name='prod-vpc-3', subnetwork_name='prod-net-3')              |
| dest_vpc      | namedtuple           | VpcDetails(project_id='yoyo-1020', vpc_name='prod-vpc-3', subnetwork_name='prod-net-3')              |
| src_location  | namedtuple           | GeographicDetails(continent='America', country='usa', region='California', city='Santa Teresa')      |
| dest_location | namedtuple           | GeographicDetails(continent='America', country='usa', region='California', city='Santa Teresa')      |
```

`gcp_flowlogs_reader.Reader` acts as an iterator over flows from the logs:

```python

from gcp_flowlogs_reader import Reader

credentials_path = '/home/service-account-.json'
ip_set = set()
for flow_record in Reader(service_account_json=credentials_path):
    ip_set.add(flow_record.src_ip)
    ip_set.add(flow_record.dest_ip)

print(len(ip_set))
```

You may pass in these keywords as arguments to affect what flows are returned:
* `log_name` - defaults to `projects/{#project_id}/logs/compute.googleapis.com%2Fvpc_flows`
* `start_time` - defaults to one hour ago
* `end_time` - defaults to now
* `filters` - defaults the the standard log name
* `collect_multiple_projects` - defaults to False
* `logging_client` - a custom `google.cloud.logging.Client` instance
* `service_account_json` - the path to a service account JSON credential file
* `service_account_info` - the service account information parsed from a credential file. See [`from_service_account`](https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.service_account.html#google.oauth2.service_account.Credentials.from_service_account_info) for more information.
