from setuptools import setup, find_packages

setup(
    name='gcp_flowlogs_reader',
    version='0.6.2',
    license='Apache',
    url='https://github.com/obsrvbl/gcp-flowlogs-reader',
    description='Reader for Google Cloud VPC Flow Logs',
    long_description=(
        "This project provides a convenient interface for accessing "
        "VPC Flow Logs stored in Google Cloud's Stackdriver Logging service."
    ),
    long_description_content_type='text/x-rst',
    author='Cisco Stealthwatch Cloud',
    author_email='support@observable.net',
    classifiers=[
        'Intended Audience :: Developers',
        'Intended Audience :: Information Technology',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
    entry_points={
        'console_scripts': [
            'gcp_flowlogs_reader = gcp_flowlogs_reader.__main__:main',
        ],
    },
    packages=find_packages(exclude=[]),
    test_suite='tests',
    install_requires=[
        'google-cloud-logging>=1.6.0,<2.0.0',
        'google-cloud-resource-manager>=0.28.3',
    ],
    tests_require=[],
)
