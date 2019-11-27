#!/usr/bin/env python
"""Scalyr Agent Plugin Module - Windows Process Metrics

This module extends the ScalyrMonitor base class to implement it's functionality, a process
metrics collector for the Windows (Server 2003 and newer) platforms, as a monitor plugin into
the Scalyr plugin framework.

The two most important object in this monitor are:

 1. The METRICS list; which defines the metrics that this module will collect
 2. The ProcessMonitor class which drives the collection of each defined metric and emits 
    its associated value at a specified sampling rate.


>>> import re, operator, collections
>>> metric_template = "{metric.metric_name} - {metric.description} {metric.units}".format
>>> criteria = dict(
...     category = 'cpu',
...     metric_name = 'winproc.disk.*',
...     match = any
... )
>>> predicates = [(operator.itemgetter(k), re.compile(v))
...                 for k,v in criteria.items() 
...                 if k is not 'match']
>>> Telemetry = collections.namedtuple('Telemetry', 'match metric attribute fetcher matcher')
>>> for metric in METRICS:
...     matches = []
...     for fetcher, matcher in predicates:
...         attribute = fetcher(metric)
...         match = matcher.search(attribute)
...         matches.append(Telemetry(match, metric, attribute, fetcher, matcher))
...     else:
...         if any(itertools.ifilter(operator.attrgetter('match'), matches)):
...             print metric_template(metric)


>>> from scalyr_agent import run_monitor
>>> monitors_path = path.join(path.dirname(scalyr_agent.__file__), 'builtin_monitors')
>>> cmdline = ['-p', monitors_path, -c, '{commandline:cmd}', 'windows_process_metrics' ]
>>> parser = run_monitor.create_parser()
>>> options, args = parser.parse_args(cmdline)
>>> run_monitor.run_standalone_monitor(args[0], options.monitor_module, options.monitors_path, 
...     options.monitor_config options.monitor_sample_interval)
0
>>> 

Author: Scott Sullivan <guy.hoozdis+scalyr@gmail.com>
License: Apache 2.0
------------------------------------------------------------------------
Copyright 2014 Scalyr Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
------------------------------------------------------------------------
"""

__author__ = "Scott Sullivan <guy.hoozdis@gmail.com>"
__version__ = "0.0.1"


__monitor__ = __name__


import os
import re
import datetime
import time

import itertools
from operator import methodcaller, attrgetter
from collections import namedtuple

try:
    import psutil
except ImportError:
    psutil = None

from scalyr_agent import ScalyrMonitor, UnsupportedSystem
from scalyr_agent import define_config_option, define_metric, define_log_field


#
# Monitor Configuration - defines the runtime environment and resources available
#
CONFIG_OPTIONS = [
    dict(
        option_name="module",
        option_description="Always ``scalyr_agent.builtin_monitors.windows_process_metrics``",
        convert_to=str,
        required_option=True,
    ),
    dict(
        option_name="commandline",
        option_description="A regular expression which will match the command line or name of the process you're "
        "interested in, as shown in the output of ``tasklist`` or ``wmic process list``. (If "
        "multiple processes match the same command line pattern, only one will be monitored.)",
        default=None,
        convert_to=str,
    ),
    dict(
        option_name="pid",
        option_description="The pid of the process from which the monitor instance will collect metrics.  This is "
        "ignored if the ``commandline`` is specified.",
        default=None,
        convert_to=str,
    ),
    dict(
        option_name="id",
        option_description="Included in each log message generated by this monitor, as a field named ``instance``. "
        "Allows you to distinguish between values recorded by different instances of this monitor.",
        required_option=True,
        convert_to=str,
    ),
]

_ = [
    define_config_option(__monitor__, **option) for option in CONFIG_OPTIONS
]  # pylint: disable=star-args
## End Monitor Configuration
# #########################################################################################


# #########################################################################################
# #########################################################################################
# ## Process's Metrics / Dimensions -
# ##
# ##    Metrics define the capibilities of this monitor.  These some utility functions
# ##    along with the list(s) of metrics themselves.
# ##
def _gather_metric(method, attribute=None, transform=None):
    """Curry arbitrary process metric extraction

    @param method: a callable member of the process object interface
    @param attribute: an optional data member, of the data structure returned by ``method``
    @param transform: an optional function that can be used to transform the value returned by ``method``.
        The function should take a single argument and return the value to report as the metric value.

    @type method callable
    @type attribute str
    @type transform: func()
    """

    doc = "Extract the {} attribute from the given process object".format
    if attribute:
        doc = "Extract the {}().{} attribute from the given process object".format

    def gather_metric(process):
        """Dynamically Generated """
        errmsg = (
            "Only the 'psutil.Process' interface is supported currently; not {}".format
        )
        proc_type = type(process)
        assert proc_type is psutil.Process, errmsg(proc_type)
        metric = methodcaller(method)  # pylint: disable=redefined-outer-name
        if attribute is not None:
            value = attrgetter(attribute)(metric(process))
        else:
            value = metric(process)

        if transform is not None:
            value = transform(value)

        return value

    # XXX: For some reason this was causing trouble for the documentation build process
    # gather_metric.__doc__ = doc(method, attribute)
    return gather_metric


# TODO:  I believe this function can be deleted.
def uptime(start_time):
    """Calculate the difference between now() and the given create_time.

    @param start_time: milliseconds passed since 'event' (not since epoc)
    @type float
    """
    return datetime.datetime.now() - datetime.datetime.fromtimestamp(start_time)


def uptime_from_start_time(start_time):
    """Returns the uptime for the process given its ``start_time``.

    @param start_time: The time the process started in seconds past epoch.
    @type start_time: float

    @return: The seconds since the process started.
    @rtype: float
    """
    return time.time() - start_time


METRIC = namedtuple("METRIC", "config dispatch")
METRIC_CONFIG = dict  # pylint: disable=invalid-name
GATHER_METRIC = _gather_metric


# pylint: disable=bad-whitespace
# =================================================================================
# ============================    Process CPU    ==================================
# =================================================================================
_PROCESS_CPU_METRICS = [
    METRIC(  ## ------------------ User-mode CPU ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.cpu",
            description="The number of seconds the CPU has spent executing instructions in user space.",
            category="CPU",
            unit="secs",
            cumulative=True,
            extra_fields={"type": "user"},
        ),
        GATHER_METRIC("cpu_times", "user"),
    ),
    METRIC(  ## ------------------ Kernel-mode CPU ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.cpu",
            description="The number of seconds the CPU has spent executing instructions in kernel space.",
            category="CPU",
            unit="secs",
            cumulative=True,
            extra_fields={"type": "system"},
        ),
        GATHER_METRIC("cpu_times", "system"),
    ),
    # TODO: Additional attributes for this section
    #  * context switches
    #  * ...
]


# =================================================================================
# ========================    Process Attributes    ===============================
# =================================================================================
_PROCESS_ATTRIBUTE_METRICS = [
    METRIC(  ## ------------------  Process Uptime   ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.uptime",
            description="The number of seconds since the process was created.",
            category="General",
            unit="seconds",
            cumulative=True,
            extra_fields={},
        ),
        GATHER_METRIC("create_time", transform=uptime_from_start_time),
    ),
    METRIC(  ## ------------------  Process Threads   ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.threads",
            description="The number of threads being used by the process.",
            category="General",
            extra_fields={},
        ),
        GATHER_METRIC("num_threads"),
    ),
    # TODO: Additional attributes for this section
    #  * number of handles
    #  * number of child processes
    #  * process priority
    #  * process cmdline
    #  * procress working directory
    #  * process env vars
    #  * parent PID
    #  * cpu affinity
]

# =================================================================================
# ========================    Process Memory    ===================================
# =================================================================================
_PROCESS_MEMORY_METRICS = [
    METRIC(  ## ------------------ Working Set ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The number of bytes of physical memory used by the process's working set.  This "
            "is the amount of memory that needs to be paged in for the process to execute.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "working_set"},
        ),
        GATHER_METRIC("memory_info_ex", "wset"),
    ),
    METRIC(  ## ------------------ Peak Working Set ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The peak working set size for the process since creation time, in bytes.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "peak_working_set"},
        ),
        GATHER_METRIC("memory_info_ex", "peak_wset"),
    ),
    METRIC(  ## ------------------ Paged Pool ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The paged-pool usage, in bytes.  This is the amount of bytes of swappable memory in use.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "paged_pool"},
        ),
        GATHER_METRIC("memory_info_ex", "paged_pool"),
    ),
    METRIC(  ## ------------------ Peak Paged Pool ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The peak paged-pool usage, in bytes.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "peak_paged_pool"},
        ),
        GATHER_METRIC("memory_info_ex", "peak_paged_pool"),
    ),
    METRIC(  ## ------------------ NonPaged Pool ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The nonpaged pool usage, in bytes.  This is the amount of memory in use that cannot be "
            "swapped out to disk.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "nonpaged_pool"},
        ),
        GATHER_METRIC("memory_info_ex", "nonpaged_pool"),
    ),
    METRIC(  ## ------------------ Peak NonPaged Pool ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The peak nonpaged pool usage, in bytes.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "peak_nonpaged_pool"},
        ),
        GATHER_METRIC("memory_info_ex", "peak_nonpaged_pool"),
    ),
    METRIC(  ## ------------------ Pagefile ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The current pagefile usage, in bytes.  The is the total number of bytes the system has "
            "committed for this running process.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "pagefile"},
        ),
        GATHER_METRIC("memory_info_ex", "pagefile"),
    ),
    METRIC(  ## ------------------ Peak Pagefile ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The peak pagefile usage, in bytes.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "peak_pagefile"},
        ),
        GATHER_METRIC("memory_info_ex", "peak_pagefile"),
    ),
    METRIC(  ## ------------------ Resident size ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The current resident size in bytes.  This should be the same as the working set.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "rss"},
        ),
        GATHER_METRIC("memory_info", "rss"),
    ),
    METRIC(  ## ------------------ Virtual memory size ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.mem.bytes",
            description="The current virtual memory size in bytes.  This does not include shared pages.",
            category="Memory",
            unit="bytes",
            extra_fields={"type": "vms"},
        ),
        GATHER_METRIC("memory_info", "vms"),
    ),
    # TODO: Additional attributes for this section
    #  * ...
]


# =================================================================================
# =============================    DISK IO    =====================================
# =================================================================================
_PROCESS_DISK_IO_METRICS = [
    METRIC(  ## ------------------ Disk Read Operations ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.disk.ops",
            description="The number of disk read requests issued by the process since creation time.",
            category="Disk",
            unit="requests",
            cumulative=True,
            extra_fields={"type": "read"},
        ),
        GATHER_METRIC("io_counters", "read_count"),
    ),
    METRIC(  ## ------------------ Disk Write Operations ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.disk.ops",
            description="The number of disk write requests issued by the process since creation time.",
            category="Disk",
            unit="requests",
            cumulative=True,
            extra_fields={"type": "write"},
        ),
        GATHER_METRIC("io_counters", "write_count"),
    ),
    METRIC(  ## ------------------ Disk Read Bytes ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.disk.bytes",
            description="The number of bytes read from disk by the process.",
            category="Disk",
            unit="bytes",
            cumulative=True,
            extra_fields={"type": "read"},
        ),
        GATHER_METRIC("io_counters", "read_bytes"),
    ),
    METRIC(  ## ------------------ Disk Read Bytes ----------------------------
        METRIC_CONFIG(
            metric_name="winproc.disk.bytes",
            description="The number of bytes written to disk by the process.",
            category="Disk",
            unit="bytes",
            cumulative=True,
            extra_fields={"type": "write"},
        ),
        GATHER_METRIC("io_counters", "write_bytes"),
    )
    # TODO: Additional attributes for this section
    #  * ...
]
# pylint: enable=bad-whitespace

METRICS = (
    _PROCESS_CPU_METRICS
    + _PROCESS_ATTRIBUTE_METRICS
    + _PROCESS_MEMORY_METRICS
    + _PROCESS_DISK_IO_METRICS
)
_ = [
    define_metric(__monitor__, **metric.config) for metric in METRICS
]  # pylint: disable=star-args


#
# Logging / Reporting - defines the method and content in which the metrics are reported.
#
define_log_field(__monitor__, "monitor", "Always ``windows_process_metrics``.")
define_log_field(
    __monitor__,
    "instance",
    "The ``id`` value from the monitor configuration, e.g. ``iis``.",
)
define_log_field(
    __monitor__,
    "app",
    "Same as ``instance``; provided for compatibility with the original Scalyr Agent.",
)
define_log_field(
    __monitor__, "metric", 'The name of a metric being measured, e.g. "winproc.cpu".'
)
define_log_field(__monitor__, "value", "The metric value.")


#
#
#
def commandline_matcher(regex, flags=re.IGNORECASE):
    """
    @param regex: a regular expression to compile and use to search process commandlines for matches
    @param flags: modify the regular expression with standard flags (see ``re`` module)

    @type regex str
    @type flags int
    """
    pattern = re.compile(regex, flags)

    def _cmdline(process):
        """Compose the process's commandline parameters as a string"""
        return " ".join(process.cmdline())

    def _match_generator(processes):
        """
        @param processes: an iterable list of process object interfaces
        @type interface
        """
        for process in processes:
            try:
                if pattern.search(process.name()) or pattern.search(_cmdline(process)):
                    return process
            except psutil.AccessDenied:
                # Just skip this process if we don't have access to it.
                continue
        return None

    return _match_generator


class ProcessMonitor(ScalyrMonitor):
    """This agent monitor plugin records CPU consumption, memory usage, and other metrics for a specified process on
    Windows system.

    You can use this plugin to record resource usage for a web server, database, or other application.
    """

    def __init__(self, monitor_config, logger, **kw):
        """TODO: Function documentation
        """
        if psutil is None:
            raise UnsupportedSystem(
                "windows_process_metrics",
                'You must install the python module "psutil" to use this module.  Typically, this'
                "can be done with the following command:"
                "  pip install psutil",
            )

        sampling_rate = kw.get("sampling_interval_secs", 30)
        global_config = kw.get("global_config")
        super(ProcessMonitor, self).__init__(
            monitor_config, logger, sampling_rate, global_config=global_config
        )
        self.__process = None

    def _initialize(self):
        self.__id = self._config.get("id", required_field=True, convert_to=str)

    def _select_target_process(self):
        """TODO: Function documentation
        """
        process = None
        if "commandline" in self._config:
            matcher = commandline_matcher(self._config["commandline"])
            process = matcher(psutil.process_iter())
        elif "pid" in self._config:
            if "$$" == self._config.get("pid"):
                pid = os.getpid()
            else:
                pid = self._config.get("pid")
            process = psutil.Process(int(pid))

        self.__process = process

    def gather_sample(self):
        """TODO: Function documentation
        """
        try:
            self._select_target_process()
            for idx, metric in enumerate(METRICS):

                if not self.__process:
                    break

                metric_name = metric.config["metric_name"]
                metric_value = metric.dispatch(self.__process)
                extra_fields = metric.config["extra_fields"]
                if extra_fields is None:
                    extra_fields = {}
                extra_fields["app"] = self.__id

                self._logger.emit_value(
                    metric_name, metric_value, extra_fields=extra_fields
                )
        except psutil.NoSuchProcess:
            self.__process = None
