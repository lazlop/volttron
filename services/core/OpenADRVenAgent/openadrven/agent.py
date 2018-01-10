# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:

# Copyright (c) 2017, Battelle Memorial Institute
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in
#    the documentation and/or other materials provided with the
#    distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and documentation
# are those of the authors and should not be interpreted as representing
# official policies, either expressed or implied, of the FreeBSD
# Project.
#
# This material was prepared as an account of work sponsored by an
# agency of the United States Government.  Neither the United States
# Government nor the United States Department of Energy, nor Battelle,
# nor any of their employees, nor any jurisdiction or organization that
# has cooperated in the development of these materials, makes any
# warranty, express or implied, or assumes any legal liability or
# responsibility for the accuracy, completeness, or usefulness or any
# information, apparatus, product, software, or process disclosed, or
# represents that its use would not infringe privately owned rights.
#
# Reference herein to any specific commercial product, process, or
# service by trade name, trademark, manufacturer, or otherwise does not
# necessarily constitute or imply its endorsement, recommendation, or
# favoring by the United States Government or any agency thereof, or
# Battelle Memorial Institute. The views and opinions of authors
# expressed herein do not necessarily state or reflect those of the
# United States Government or any agency thereof.
#
# PACIFIC NORTHWEST NATIONAL LABORATORY
# operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
# under Contract DE-AC05-76RL01830
# }}}

from __future__ import print_function

from collections import namedtuple
from datetime import datetime as dt
from datetime import timedelta
from dateutil import parser
import gevent
# OpenADR rule 1: use ISO8601 timestamp
import json
import logging
import lxml.etree as etree_
import os
import random
import requests
from requests.exceptions import ConnectionError
import signxml
import StringIO
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from volttron.platform.agent import utils
# OpenADR rule 1: use ISO8601 timestamp
from volttron.platform.agent.utils import format_timestamp
from volttron.platform.messaging import topics, headers
from volttron.platform.vip.agent import Agent, Core, RPC

from oadr_builder import *
from oadr_extractor import *
from oadr_20b import parseString, oadrSignedObject
from oadr_common import *
from models import ORMBase
from models import EiEvent, EiReport, EiTelemetryValues

utils.setup_logging()
_log = logging.getLogger(__name__)

__version__ = '1.0'

ENDPOINT_BASE = '/OpenADR2/Simple/2.0b/'
EIEVENT = ENDPOINT_BASE + 'EiEvent'
EIREPORT = ENDPOINT_BASE + 'EiReport'
EIREGISTERPARTY = ENDPOINT_BASE + 'EiRegisterParty'
POLL = ENDPOINT_BASE + 'OadrPoll'

Endpoint = namedtuple('Endpoint', ['url', 'callback'])
OPENADR_ENDPOINTS = {
    'EiEvent': Endpoint(url=EIEVENT, callback='push_request'),
    'EiReport': Endpoint(url=EIREPORT, callback='push_request'),
    'EiRegisterParty': Endpoint(url=EIREGISTERPARTY, callback='push_request')}

VTN_REQUESTS = {
    'oadrDistributeEvent': 'handle_oadr_distribute_event',
    'oadrRegisterReport': 'handle_oadr_register_report',
    'oadrRegisteredReport': 'handle_oadr_registered_report',
    'oadrCreateReport': 'handle_oadr_create_report',
    'oadrUpdatedReport': 'handle_oadr_updated_report',
    'oadrCancelReport': 'handle_oadr_cancel_report',
    'oadrResponse': 'handle_oadr_response',
    'oadrCreatedPartyRegistration': 'handle_oadr_created_party_registration'}

PROCESS_LOOP_FREQUENCY_SECS = 5
DEFAULT_REPORT_INTERVAL_SECS = 15
DEFAULT_OPT_IN_TIMEOUT_SECS = 30 * 60       # If no optIn timeout was configured, use 30 minutes.

# These parameters control behavior that is sometimes temporarily disabled during software development.
USE_REPORTS = True
SEND_POLL = True

# Paths to sample X509 certificates, generated by Kyrio. These are needed when security_level = 'high'.
CERTS_DIRECTORY = '$VOLTTRON_ROOT/services/core/OpenADRVenAgent/certs/'
CERT_FILENAME = CERTS_DIRECTORY + 'TEST_RSA_VEN_171024145702_cert.pem'
KEY_FILENAME = CERTS_DIRECTORY + 'TEST_RSA_VEN_171024145702_privkey.pem'
VTN_CA_CERT_FILENAME = CERTS_DIRECTORY + 'TEST_OpenADR_RSA_BOTH0002_Cert.pem'


def ven_agent(config_path, **kwargs):
    """
        Parse the OpenADRVenAgent configuration file and return an instance of
        the agent that has been created using that configuration.

        See initialize_config() method documentation for a description of each configurable parameter.

    :param config_path: (str) Path to a configuration file.
    :returns: OpenADRVenAgent instance
    """
    try:
        config = utils.load_config(config_path)
    except StandardError, err:
        _log.error("Error loading configuration: {}".format(err))
        config = {}
    db_path = config.get('db_path')
    ven_id = config.get('ven_id')
    ven_name = config.get('ven_name')
    vtn_id = config.get('vtn_id')
    vtn_address = config.get('vtn_address')
    send_registration = config.get('send_registration')
    security_level = config.get('security_level')
    poll_interval_secs = config.get('poll_interval_secs')
    log_xml = config.get('log_xml')
    opt_in_timeout_secs = config.get('opt_in_timeout_secs')
    opt_in_default_decision = config.get('opt_in_default_decision')
    request_events_on_startup = config.get('request_events_on_startup')
    report_parameters = config.get('report_parameters')
    return OpenADRVenAgent(db_path, ven_id, ven_name, vtn_id, vtn_address, send_registration, security_level,
                           poll_interval_secs, log_xml, opt_in_timeout_secs, opt_in_default_decision,
                           request_events_on_startup, report_parameters, **kwargs)


class OpenADRVenAgent(Agent):
    """
        OpenADR (Automated Demand Response) is a standard for alerting and responding
        to the need to adjust electric power consumption in response to fluctuations
        in grid demand.

        For further information about OpenADR and this agent, please see
        the OpenADR documentation in VOLTTRON ReadTheDocs.

        OpenADR communications are conducted between Virtual Top Nodes (VTNs) and Virtual End Nodes (VENs).
        In this implementation, a VOLTTRON agent is a VEN, implementing EiEvent and EiReport services
        in conformance with a subset of the OpenADR 2.0b specification.

        The VEN receives VTN requests via the VOLTTRON web service.

        The VTN can 'call an event', indicating that a load-shed event should occur.
        The VEN responds with an 'optIn' acknowledgment.

        In conjunction with an event (or independent of events), the VEN reports device status
        and usage telemetry, relying on data received periodically from other VOLTTRON agents.

        Events:
            The VEN agent maintains a persistent record of DR events.
            Event updates (including creation) trigger publication of event JSON on the VOLTTRON message bus.
            Other VOLTTRON agents can also call a get_events() RPC to retrieve the current status
            of particular events, or of all active events.

        Reporting:
            The VEN agent configuration defines telemetry values (data points) to be reported to the VTN.
            The VEN agent maintains a persistent record of reportable/reported telemetry values over time.
            Other VOLTTRON agents are expected to call a report_telemetry() RPC to supply the VEN agent
            with a regular stream of telemetry values for reporting.
            Other VOLTTRON agents can receive notification of changes in telemetry reporting requirements
            by subscribing to publication of telemetry parameters.

        Pub/Sub (see method documentation):
            publish_event()
            publish_telemetry_parameters_for_report()

        RPC calls (see method documentation):
            respond_to_event(event_id, opt_in=True):
            get_events(in_progress_only=True, started_after=None, end_time_before=None)
            get_telemetry_parameters()
            set_telemetry_status(online, manual_override)
            report_telemetry(telemetry_values)

        Supported requests/responses in the OpenADR VTN interface:
            VTN:
                oadrDistributeEvent (needed for event cancellation)
                oadrResponse
                oadrRegisteredReport
                oadrCreateReport
                oadrUpdatedReport
                oadrCancelReport
                oadrCreatedPartyRegistration
            VEN:
                oadrPoll
                oadrRequestEvent
                oadrCreatedEvent
                oadrResponse
                oadrRegisterReport
                oadrCreatedReport
                oadrUpdateReport
                oadrCanceledReport
                oadrCreatePartyRegistration
                oadrQueryRegistration
    """

    _db_session = None
    _last_poll = None
    _active_events = {}
    _active_reports = {}

    def __init__(self, db_path, ven_id, ven_name, vtn_id, vtn_address, send_registration, security_level,
                 poll_interval_secs, log_xml, opt_in_timeout_secs, opt_in_default_decision,
                 request_events_on_startup, report_parameters,
                 **kwargs):
        super(OpenADRVenAgent, self).__init__(enable_web=True, **kwargs)

        self.db_path = None
        self.ven_id = None
        self.ven_name = None
        self.vtn_id = None
        self.vtn_address = None
        self.send_registration = False
        self.security_level = None
        self.poll_interval_secs = None
        self.log_xml = True
        self.opt_in_timeout_secs = None
        self.opt_in_default_decision = 'optIn'
        self.request_events_on_startup = None
        self.report_parameters = {}
        self.default_config = {"db_path": db_path,
                               "ven_id": ven_id,
                               "ven_name": ven_name,
                               "vtn_id": vtn_id,
                               "vtn_address": vtn_address,
                               "send_registration": send_registration,
                               "security_level": security_level,
                               "poll_interval_secs": poll_interval_secs,
                               "log_xml": log_xml,
                               "opt_in_timeout_secs": opt_in_timeout_secs,
                               "opt_in_default_decision": opt_in_default_decision,
                               "request_events_on_startup": request_events_on_startup,
                               "report_parameters": report_parameters}
        self.vip.config.set_default("config", self.default_config)
        self.vip.config.subscribe(self._configure, actions=["NEW", "UPDATE"], pattern="config")
        self.initialize_config(self.default_config)
        # State variables for VTN request/response processing
        self.oadr_current_service = None
        self.oadr_current_request_id = None
        # The following parameters can be adjusted by issuing a set_telemetry_status() RPC call.
        self.ven_online = 'false'
        self.ven_manual_override = 'false'

    def _configure(self, config_name, action, contents):
        """The agent's config may have changed. Re-initialize it."""
        config = self.default_config.copy()
        config.update(contents)
        self.initialize_config(config)

    def initialize_config(self, config):
        """
            Initialize the agent's configuration.

            Configuration parameters (see openadrven.config for a sample config file):

                db_path:                Pathname of the agent's sqlite database.
                                        ~ and shell variables will be expanded if present.
                ven_id:                 (string) OpenADR ID of this virtual end node. Identifies this VEN to the VTN.
                ven_name:               Name of this virtual end node. Identifies this VEN during registration,
                                        before its ID is known.
                vtn_id:                 (string) OpenADR ID of the VTN with which this VEN communicates.
                vtn_address:            URL and port number of the VTN.
                send_registration:      ('True' or 'False') If 'True', send a one-time registration request to the VTN,
                                        obtaining the VEN ID. The agent should be run in this mode initially,
                                        then shut down and run with this parameter set to 'False' thereafter.
                security_level:         If 'high', the VTN and VEN use a third-party signing authority to sign
                                        and authenticate each request.
                                        Default is 'standard' (XML payloads do not contain Signature elements).
                poll_interval_secs:     (integer) How often the VEN should send an OadrPoll to the VTN.
                log_xml:                ('True' or 'False') Whether to write inbound/outbound XML to the agent's log.
                opt_in_timeout_secs:    (integer) How long to wait before making a default optIn/optOut decision.
                opt_in_default_decision: ('True' or 'False') What optIn/optOut choice to make by default.
                request_events_on_startup: ('True' or 'False') Whether to send oadrRequestEvent to the VTN on startup.
                report_parameters:      A dictionary of definitions of reporting/telemetry parameters.
        """
        _log.debug("Configuring agent")
        self.db_path = config.get('db_path')
        self.ven_id = config.get('ven_id')
        self.ven_name = config.get('ven_name')
        self.vtn_id = config.get('vtn_id')
        self.vtn_address = config.get('vtn_address')
        self.send_registration = (config.get('send_registration') == 'True')
        self.security_level = config.get('security_level')
        self.log_xml = (config.get('log_xml') != 'False')
        opt_in_timeout = config.get('opt_in_timeout_secs')
        self.opt_in_timeout_secs = int(opt_in_timeout if opt_in_timeout else DEFAULT_OPT_IN_TIMEOUT_SECS)
        self.opt_in_default_decision = config.get('opt_in_default_decision')
        loop_frequency = config.get('poll_interval_secs')
        self.poll_interval_secs = int(loop_frequency if loop_frequency else PROCESS_LOOP_FREQUENCY_SECS)
        self.request_events_on_startup = (config.get('request_events_on_startup') == 'True')
        self.report_parameters = config.get('report_parameters')

        # Validate and adjust the configuration parameters.
        if type(self.db_path) == str:
            self.db_path = os.path.expanduser(self.db_path)
            self.db_path = os.path.expandvars(self.db_path)
        try:
            self.opt_in_timeout_secs = int(self.opt_in_timeout_secs)
        except ValueError:
            # If opt_in_timeout_secs was not supplied or was not an integer, default to a 10-minute timeout.
            self.opt_in_timeout_secs = 600

        if self.poll_interval_secs < PROCESS_LOOP_FREQUENCY_SECS:
            _log.warning('Poll interval is too frequent: resetting it to {}'.format(PROCESS_LOOP_FREQUENCY_SECS))
            self.poll_interval_secs = PROCESS_LOOP_FREQUENCY_SECS

        _log.info('Configuration parameters:')
        _log.info('\tDatabase = {}'.format(self.db_path))
        _log.info('\tVEN ID = {}'.format(self.ven_id))
        _log.info('\tVEN name = {}'.format(self.ven_name))
        _log.info('\tVTN ID = {}'.format(self.vtn_id))
        _log.info('\tVTN address = {}'.format(self.vtn_address))
        _log.info('\tSend registration = {}'.format(self.send_registration))
        _log.info('\tSecurity level = {}'.format(self.security_level))
        _log.info('\tPoll interval = {} seconds'.format(self.poll_interval_secs))
        _log.info('\tLog XML = {}'.format(self.log_xml))
        _log.info('\toptIn timeout (secs) = {}'.format(self.opt_in_timeout_secs))
        _log.info('\toptIn default decision = {}'.format(self.opt_in_default_decision))
        _log.info('\tRequest events on startup = {}'.format(self.request_events_on_startup))
        _log.info("\treport parameters = {}".format(self.report_parameters))

    @Core.receiver('onstart')
    def onstart_method(self, sender):
        """The agent has started. Perform initialization and spawn the main process loop."""
        _log.debug('Starting agent')

        self.register_endpoints()

        if self.send_registration:
            # VEN registration with the VTN server.
            # Register the VEN, obtaining the VEN ID. This is a one-time action.
            self.send_oadr_create_party_registration()
        else:
            # Schedule an hourly database-cleanup task.
            self.core.periodic(60 * 60, self.telemetry_cleanup)

            # Populate the caches with all of the database's events and reports that are active.
            for event in self._get_events():
                _log.debug('Re-caching event with ID {}'.format(event.event_id))
                self._active_events[event.event_id] = event
            for report in self._get_reports():
                _log.debug('Re-caching report with ID {}'.format(report.report_request_id))
                self._active_reports[report.report_request_id] = report

            try:
                if self.request_events_on_startup:
                    # After a restart, the VEN asks the VTN for the status of all current events.
                    # When this is sent to the EPRI VTN server, it returns a 500 and logs a "method missing" traceback.
                    self.send_oadr_request_event()

                if USE_REPORTS:
                    # Send an initial report-registration request to the VTN.
                    self.send_oadr_register_report()
            except Exception, err:
                _log.error('Error in agent startup: {}'.format(err), exc_info=True)
            self.core.periodic(PROCESS_LOOP_FREQUENCY_SECS, self.main_process_loop)

    def main_process_loop(self):
        """
            gevent thread. Perform periodic tasks, executing them serially.

            Periodic tasks include:
                Poll the VTN server.
                Perform event-management tasks:
                    Force an optIn/optOut decision if too much time has elapsed.
                    Transition event state when appropriate.
                    Expire events that have become completed or canceled.
                Perform report-management tasks:
                    Send telemetry to the VTN for any active report.
                    Transition report state when appropriate.
                    Expire reports that have become completed or canceled.

            This is intended to be a long-running gevent greenlet -- it should never crash.
            If exceptions occur, they are logged, but no process failure occurs.
        """
        try:
            # If it's been poll_interval_secs since the last poll request, issue a new one.
            if self._last_poll is None or \
                    ((utils.get_aware_utc_now() - self._last_poll).total_seconds() > self.poll_interval_secs):
                if SEND_POLL:
                    self.send_oadr_poll()

            for event in self.active_events():
                self.process_event(event)

            if USE_REPORTS:
                for report in self.active_reports():
                    self.process_report(report)

        except Exception, err:
            _log.error('Error in main process loop: {}'.format(err), exc_info=True)

    def process_event(self, evt):
        """
            Perform periodic maintenance for an event that's in the cache.

            Transition its state when appropriate.
            Expire it from the cache if it has become completed or canceled.

        @param evt: An EiEvent instance.
        """
        now = utils.get_aware_utc_now()
        if evt.is_active():
            if evt.end_time is not None and now > evt.end_time:
                _log.debug('Setting event {} to status {}'.format(evt.event_id, evt.STATUS_COMPLETED))
                self.set_event_status(evt, evt.STATUS_COMPLETED)
                self.publish_event(evt)
            else:
                if evt.status == evt.STATUS_ACTIVE:
                    # It's an active event. Which is fine; nothing special needs to be done here.
                    pass
                else:
                    if now > evt.start_time and evt.opt_type == evt.OPT_TYPE_OPT_IN:
                        _log.debug('Setting event {} to status {}'.format(evt.event_id, evt.STATUS_ACTIVE))
                        self.set_event_status(evt, evt.STATUS_ACTIVE)
                        self.publish_event(evt)
        else:
            # Expire events from the cache if they're completed or canceled.
            _log.debug('Expiring event {}'.format(evt.event_id))
            self.expire_event(evt)

    def process_report(self, rpt):
        """
            Perform periodic maintenance for a report that's in the cache.

            Send telemetry to the VTN if the report is active.
            Transition its state when appropriate.
            Expire it from the cache if it has become completed or canceled.

        @param rpt: An EiReport instance.
        """
        if rpt.is_active():
            now = utils.get_aware_utc_now()
            if rpt.status == rpt.STATUS_ACTIVE:
                if rpt.end_time is None or rpt.end_time > now:
                    rpt_interval = rpt.interval_secs if rpt.interval_secs is not None else DEFAULT_REPORT_INTERVAL_SECS
                    next_report_time = rpt.last_report + timedelta(seconds=rpt_interval)
                    if utils.get_aware_utc_now() > next_report_time:
                        # Possible enhancement: Use a periodic gevent instead of a timeout?
                        self.send_oadr_update_report(rpt)
                        if rpt_interval == 0:
                            # OADR rule 324: If rpt_interval == 0 it's a one-time report, so set status to COMPLETED.
                            rpt.status = rpt.STATUS_COMPLETED
                            self.commit()
                else:
                    _log.debug('Setting report {} to status {}'.format(rpt.report_request_id, rpt.STATUS_COMPLETED))
                    self.set_report_status(rpt, rpt.STATUS_COMPLETED)
                    self.publish_telemetry_parameters_for_report(rpt)
            else:
                if rpt.start_time < now and (rpt.end_time is None or now < rpt.end_time):
                    _log.debug('Setting report {} to status {}'.format(rpt.report_request_id, rpt.STATUS_ACTIVE))
                    self.set_report_status(rpt, rpt.STATUS_ACTIVE)
                    self.publish_telemetry_parameters_for_report(rpt)
        else:
            # Expire reports from the cache if they're completed or canceled.
            _log.debug('Expiring report {} from cache'.format(rpt.report_request_id))
            self.expire_event(rpt)

    def force_opt_type_decision(self, event_id):
        """
            Force an optIn/optOut default decision if lots of time has elapsed with no decision from the control agent.

            Scheduled gevent thread, kicked off when an event is first published.
            The default choice comes from "opt_in_default_decision" in the agent config.

        @param event_id: (String) ID of the event for which a decision will be made.
        """
        event = self.get_event_for_id(event_id)
        if event and event.is_active() and event.opt_type not in [EiEvent.OPT_TYPE_OPT_IN,
                                                                  EiEvent.OPT_TYPE_OPT_OUT]:
            event.opt_type = self.opt_in_default_decision
            self.commit()
            _log.info('Forcing an {} decision for event {}'.format(event.opt_type, event.event_id))
            if event.status == event.STATUS_ACTIVE:
                # Odd exception scenario: If the event was already active, roll its status back to STATUS_FAR.
                self.set_event_status(event, event.STATUS_FAR)
            self.publish_event(event)  # Tell the volttron message bus.
            self.send_oadr_created_event(event)  # Tell the VTN.

    # ***************** Methods for Servicing VTN Requests ********************

    def push_request(self, env, request):
        """Callback. The VTN pushed an http request. Service it."""
        _log.debug('Servicing a VTN push request')
        self.core.spawn(self.service_vtn_request, request)
        # Return an empty response.
        return [HTTP_STATUS_CODES[204], '', [("Content-Length", "0")]]

    def service_vtn_request(self, request):
        """
            An HTTP request/response was received. Handle it.

            Event workflow (see OpenADR Profile Specification section 8.1)...

            Event poll / creation:
                (VEN) oadrPoll
                (VTN) oadrDistributeEvent (all events are included; one oadrEvent element per event)
                (VEN) oadrCreatedEvent with optIn/optOut (if events had oadrResponseRequired)
                        If "always", an oadrCreatedEvent must be sent for each event.
                        If "never", it was a "broadcast" event -- never create an event in response.
                        Otherwise, respond if event state (eventID, modificationNumber) has changed.
                (VTN) oadrResponse

            Event change:
                (VEN) oadrCreatedEvent (sent if the optIn/optOut status has changed)
                (VTN) oadrResponse

            Sample oadrDistributeEvent use case from the OpenADR Program Guide:

                Event:
                    Notification: Day before event
                    Start Time: midnight
                    Duration: 24 hours
                    Randomization: None
                    Ramp Up: None
                    Recovery: None
                    Number of signals: 2
                    Signal Name: simple
                        Signal Type: level
                        Units: LevN/A
                        Number of intervals: equal TOU Tier change in 24 hours (2 - 6)
                        Interval Duration(s): TOU tier active time frame (i.e. 6 hours)
                        Typical Interval Value(s): 0 - 4 mapped to TOU Tiers (0 - Cheapest Tier)
                        Signal Target: None
                    Signal Name: ELECTRICITY_PRICE
                        Signal Type: price
                        Units: USD per Kwh
                        Number of intervals: equal TOU Tier changes in 24 hours (2 - 6)
                        Interval Duration(s): TOU tier active time frame (i.e. 6 hours)
                        Typical Interval Value(s): $0.10 to $1.00 (current tier rate)
                        Signal Target: None
                    Event Targets: venID_1234
                    Priority: 1
                    VEN Response Required: always
                    VEN Expected Response: optIn
                Reports:
                    None

            Report workflow (see OpenADR Profile Specification section 8.3)...

            Report registration interaction:
                (VEN) oadrRegisterReport (METADATA report)
                    VEN sends its reporting capabilities to VTN.
                    Each report, identified by a reportSpecifierID, is described as elements and attributes.
                (VTN) oadrRegisteredReport (with optional oadrReportRequests)
                    VTN acknowledges that capabilities have been registered.
                    VTN optionally requests one or more reports by reportSpecifierID.
                    Even if reports were previously requested, they should be requested again at this point.
                (VEN) oadrCreatedReport (if report requested)
                    VEN acknowledges that it has received the report request and is generating the report.
                    If any reports were pending delivery, they are included in the payload.
                (VTN) oadrResponse
                    Why??

            Report creation interaction:
                (VTN) oadrCreateReport
                    See above - this is like the "request" portion of oadrRegisteredReport
                (VEN) oadrCreatedReport
                    See above.

            Report update interaction - this is the actual report:
                (VEN) oadrUpdateReport (report with reportRequestID and reportSpecifierID)
                    Send a report update containing actual data values
                (VTN) oadrUpdatedReport (optional oadrCancelReport)
                    Acknowledge report receipt, and optionally cancel the report

            Report cancellation:
                (VTN) oadrCancelReport (reportRequestID)
                    This can be sent to cancel a report that is in progress.
                    It should also be sent if the VEN keeps sending oadrUpdateReport
                        after an oadrUpdatedReport cancellation.
                    If reportToFollow = True, the VEN is expected to send one final additional report.
                (VEN) oadrCanceledReport
                    Acknowledge the cancellation.
                    If any reports were pending delivery, they are included in the payload.

            Key elements in the METADATA payload:
                reportSpecifierID: Report identifier, used by subsequent oadrCreateReport requests
                rid: Data point identifier
                    This VEN reports only two data points: baselinePower, actualPower
                Duration: the amount of time that data can be collected
                SamplingRate.oadrMinPeriod: maximum sampling frequency
                SamplingRate.oadrMaxPeriod: minimum sampling frequency
                SamplingRate.onChange: whether or not data is sampled as it changes

            For an oadrCreateReport example from the OpenADR Program Guide, see test/xml/sample_oadrCreateReport.xml.

        @param request: The request's XML payload.
        """
        try:
            if self.log_xml:
                _log.debug('VTN PAYLOAD:')
                _log.debug('\n{}'.format(etree_.tostring(etree_.fromstring(request), pretty_print=True)))
            payload = parseString(request, silence=True)
            signed_object = payload.oadrSignedObject
            if signed_object is None:
                raise OpenADRInterfaceException('No SignedObject in payload', OADR_BAD_DATA)

            if self.security_level == 'high':
                # At high security, the request is accompanied by a Signature.
                # (not implemented) The VEN should use a certificate authority to validate and decode the request.
                pass

            # Call an appropriate method to handle the VTN request.
            element_name = self.vtn_request_element_name(signed_object)
            _log.debug('VTN: {}'.format(element_name))
            request_object = getattr(signed_object, element_name)
            request_method = getattr(self, VTN_REQUESTS[element_name])
            request_method(request_object)

            if request_object.__class__.__name__ != 'oadrResponseType':
                # A non-default response was received from the VTN. Issue a followup poll request.
                self.send_oadr_poll()

        except OpenADRInternalException, err:
            if err.error_code == OADR_EMPTY_DISTRIBUTE_EVENT:
                _log.warning('Error handling VTN request: {}'.format(err))          # No need for a stack trace
            else:
                _log.warning('Error handling VTN request: {}'.format(err), exc_info=True)
        except OpenADRInterfaceException, err:
            _log.warning('Error handling VTN request: {}'.format(err), exc_info=True)
            # OADR rule 48: Log the validation failure, send an oadrResponse.eiResponse with an error code.
            self.send_oadr_response(err.message, err.error_code or OADR_BAD_DATA)
        except Exception, err:
            _log.error("Error handling VTN request: {}".format(err), exc_info=True)
            self.send_oadr_response(err.message, OADR_BAD_DATA)

    @staticmethod
    def vtn_request_element_name(signed_object):
        """Given a SignedObject from the VTN, return the element name of the request that it wraps."""
        non_null_elements = [name for name in VTN_REQUESTS.keys() if getattr(signed_object, name)]
        element_count = len(non_null_elements)
        if element_count == 1:
            return non_null_elements[0]
        else:
            if element_count == 0:
                error_msg = 'Bad request {}, supported types are {}'.format(signed_object, VTN_REQUESTS.keys())
            else:
                error_msg = 'Bad request {}, too many signedObject elements'.format(signed_object)
            raise OpenADRInterfaceException(error_msg, None)

    # ***************** Handle Requests from the VTN to the VEN ********************

    def handle_oadr_created_party_registration(self, oadr_created_party_registration):
        """
            The VTN has responded to an oadrCreatePartyRegistration by sending an oadrCreatedPartyRegistration.

        @param oadr_created_party_registration: The VTN's request.
        """
        self.oadr_current_service = EIREGISTERPARTY
        self.check_ei_response(oadr_created_party_registration.eiResponse)
        extractor = OadrCreatedPartyRegistrationExtractor(registration=oadr_created_party_registration)
        _log.info('***********')
        ven_id = extractor.extract_ven_id()
        if ven_id:
            _log.info('The VTN supplied {} as the ID of this VEN (ven_id).'.format(ven_id))
        poll_freq = extractor.extract_poll_freq()
        if poll_freq:
            _log.info('The VTN requested a poll frequency of {} (poll_interval_secs).'.format(poll_freq))
        vtn_id = extractor.extract_vtn_id()
        if vtn_id:
            _log.info('The VTN supplied {} as its ID (vtn_id).'.format(vtn_id))
        _log.info('Please set these values in the VEN agent config.')
        _log.info('Registration is complete. Set send_registration to False in the VEN config and restart the agent.')
        _log.info('***********')

    def handle_oadr_distribute_event(self, oadr_distribute_event):
        """
            The VTN has responded to an oadrPoll by sending an oadrDistributeEvent.

            Create or update an event, then respond with oadrCreatedEvent.

            For sample XML, see test/xml/sample_oadrDistributeEvent.xml.

        @param oadr_distribute_event: (OadrDistributeEventType) The VTN's request.
        """
        self.oadr_current_service = EIEVENT
        self.oadr_current_request_id = None
        if getattr(oadr_distribute_event, 'eiResponse'):
            self.check_ei_response(oadr_distribute_event.eiResponse)

        # OADR rule 41: requestID does not need to be unique.
        self.oadr_current_request_id = oadr_distribute_event.requestID

        vtn_id = oadr_distribute_event.vtnID
        if vtn_id is not None and vtn_id != self.vtn_id:
            raise OpenADRInterfaceException('vtnID failed to match agent config: {}'.format(vtn_id), OADR_BAD_DATA)

        oadr_event_list = oadr_distribute_event.oadrEvent
        if len(oadr_event_list) == 0:
            raise OpenADRInternalException('oadrDistributeEvent received with no events', OADR_EMPTY_DISTRIBUTE_EVENT)

        oadr_event_ids = []
        for oadr_event in oadr_event_list:
            try:
                event = self.handle_oadr_event(oadr_event)
                if event:
                    oadr_event_ids.append(event.event_id)
            except OpenADRInterfaceException, err:
                # OADR rule 19: If a VTN message contains a mix of valid and invalid events,
                # respond to the valid ones. Don't reject the entire message due to invalid events.
                # OADR rule 48: Log the validation failure and send the error code in oadrCreatedEvent.eventResponse.
                # (The oadrCreatedEvent's eiResponse should contain a 200 -- normal -- status code.)
                _log.warning('Event error: {}'.format(err), exc_info=True)
                # Construct a temporary EIEvent to hold data that will be reported in the error return.
                if oadr_event.eiEvent and oadr_event.eiEvent.eventDescriptor:
                    event_id = oadr_event.eiEvent.eventDescriptor.eventID
                    modification_number = oadr_event.eiEvent.eventDescriptor.modificationNumber
                else:
                    event_id = None
                    modification_number = None
                error_event = EiEvent(self.oadr_current_request_id, event_id)
                error_event.modification_number = modification_number
                self.send_oadr_created_event(error_event,
                                             error_code=err.error_code or OADR_BAD_DATA,
                                             error_message=err.message)
            except Exception, err:
                _log.warning('Unanticipated error during event processing: {}'.format(err), exc_info=True)
                self.send_oadr_response(err.message, OADR_BAD_DATA)

        for agent_event in self._get_events():
            if agent_event.event_id not in oadr_event_ids:
                # "Implied cancel:"
                # OADR rule 61: If the VTN request omitted an active event, cancel it.
                # Also, think about whether to alert the VTN about this cancellation by sending it an oadrCreatedEvent.
                _log.debug('Event ID {} not in distributeEvent: canceling it.'.format(agent_event.event_id))
                self.handle_event_cancellation(agent_event, 'never')

    def handle_oadr_event(self, oadr_event):
        """
            An oadrEvent was received, usually as part of an oadrDistributeEvent. Handle the event creation/update.

            Respond with oadrCreatedEvent.

            For sample XML, see test/xml/sample_oadrDistributeEvent.xml.

        @param oadr_event: (OadrEventType) The VTN's request.
        @return: (EiEvent) The event that was created or updated.
        """

        def create_temp_event(received_ei_event):
            """Create a temporary EiEvent in preparation for an event creation or update."""
            event_descriptor = received_ei_event.eventDescriptor
            if event_descriptor is None:
                raise OpenADRInterfaceException('Missing eiEvent.eventDescriptor', OADR_BAD_DATA)
            event_id = event_descriptor.eventID
            if event_id is None:
                raise OpenADRInterfaceException('Missing eiEvent.eventDescriptor.eventID', OADR_BAD_DATA)
            _log.debug('Processing received event, ID = {}'.format(event_id))
            tmp_event = EiEvent(self.oadr_current_request_id, event_id)
            extractor = OadrEventExtractor(event=tmp_event, ei_event=received_ei_event)
            extractor.extract_event_descriptor()
            extractor.extract_active_period()
            extractor.extract_signals()
            return tmp_event

        def update_event(temp_event, event):
            """Update the current event based on the contents of temp_event."""
            _log.debug('Modification number has changed: {}'.format(temp_event.modification_number))
            # OADR rule 57: If modificationNumber increments, replace the event with the modified version.
            if event.opt_type == EiEvent.OPT_TYPE_OPT_OUT:
                # OADR rule 50: The VTN may continue to send events that the VEN has opted out of.
                pass  # Take no action, other than responding to the VTN.
            else:
                if temp_event.status == EiEvent.STATUS_CANCELED:
                    if event.status != EiEvent.STATUS_CANCELED:
                        # OADR rule 59: The event was just canceled. Process an event cancellation.
                        self.handle_event_cancellation(event, response_required)
                else:
                    event.copy_from_event(temp_event)
                    # A VEN may ignore the received event status, calculating it based on the time.
                    # OADR rule 66: Do not treat status == completed as a cancellation.
                    if event.status == EiEvent.STATUS_CANCELED and temp_event.status != EiEvent.STATUS_CANCELED:
                        # If the VEN thinks the event is canceled and the VTN doesn't think that, un-cancel it.
                        event.status = temp_event.status
                    self.commit()
                    # Tell the VOLTTRON world about the event update.
                    self.publish_event(event)

        def create_event(event):
            self.add_event(event)
            if event.status == EiEvent.STATUS_CANCELED:
                # OADR rule 60: Ignore a new event if it's cancelled - this is NOT a validation error.
                pass
            else:
                opt_deadline = utils.get_aware_utc_now() + timedelta(seconds=self.opt_in_timeout_secs)
                self.core.schedule(opt_deadline, self.force_opt_type_decision, event.event_id)
                _log.debug('Scheduled a default optIn/optOut decision for {}'.format(opt_deadline))
                self.publish_event(event)  # Tell the VOLTTRON world about the event creation.

        # Create a temporary EiEvent, constructed from the OadrDistributeEventType.
        ei_event = oadr_event.eiEvent
        response_required = oadr_event.oadrResponseRequired

        if ei_event.eiTarget and ei_event.eiTarget.venID and self.ven_id not in ei_event.eiTarget.venID:
            # Rule 22: If an eiTarget is furnished, handle the event only if this venID is in the target list.
            event = None
        else:
            temp_event = create_temp_event(ei_event)
            event = self.get_event_for_id(temp_event.event_id)
            if event:
                if temp_event.modification_number < event.modification_number:
                    _log.debug('Out-of-order modification number: {}'.format(temp_event.modification_number))
                    # OADR rule 58: Respond with error code 450.
                    raise OpenADRInterfaceException('Invalid modification number (too low)',
                                                    OADR_MOD_NUMBER_OUT_OF_ORDER)
                elif temp_event.modification_number > event.modification_number:
                    update_event(temp_event, event)
                else:
                    _log.debug('No modification number change, taking no action')
            else:
                # OADR rule 56: If the received event has an unrecognized event_id, create a new event.
                _log.debug('Creating event for ID {}'.format(temp_event.event_id))
                event = temp_event
                create_event(event)

            if response_required == 'always':
                # OADR rule 12, 62: Send an oadrCreatedEvent if response_required == 'always'.
                # OADR rule 12, 62: If response_required == 'never', do not send an oadrCreatedEvent.
                self.send_oadr_created_event(event)

        return event

    def handle_event_cancellation(self, event, response_required):
        """
            An event was canceled by the VTN. Update local state and publish the news.

        @param event: (EiEvent) The event that was canceled.
        @param response_required: (string) Indicates when the VTN expects a confirmation/response to its request.
        """
        if event.start_after:
            # OADR rule 65: If the event has a startAfter value,
            # schedule cancellation for a random future time between now and (now + startAfter).
            max_delay = isodate.parse_duration(event.start_after)
            cancel_time = utils.get_aware_utc_now() + timedelta(seconds=(max_delay.seconds * random.random()))
            self.core.schedule(cancel_time, self._handle_event_cancellation, event, response_required)
        else:
            self._handle_event_cancellation(event, response_required)

    def _handle_event_cancellation(self, event, response_required):
        """
            (Internal) An event was canceled by the VTN. Update local state and publish the news.

        @param event: (EiEvent) The event that was canceled.
        @param response_required: (string) Indicates when the VTN expects a confirmation/response to its request.
        """
        event.status = EiEvent.STATUS_CANCELED
        if response_required != 'never':
            # OADR rule 36: If response_required != never, confirm cancellation with optType = optIn.
            event.optType = event.OPT_TYPE_OPT_IN
        self.commit()
        self.publish_event(event)       # Tell VOLTTRON agents about the cancellation.

    def handle_oadr_register_report(self, request):
        """
            The VTN is sending METADATA, registering the reports that it can send to the VEN.

            Send no response -- the VEN doesn't want any of the VTN's crumby reports.

        @param request: The VTN's request.
        """
        self.oadr_current_service = EIREPORT
        self.oadr_current_request_id = None
        # OADR rule 301: Sent when the VTN wakes up.
        pass

    def handle_oadr_registered_report(self, oadr_registered_report):
        """
            The VTN acknowledged receipt of the METADATA in oadrRegisterReport.

            If the VTN requested any reports (by specifier ID), create them.
            Send an oadrCreatedReport acknowledgment for each request.

        @param oadr_registered_report: (oadrRegisteredReportType) The VTN's request.
        """
        self.oadr_current_service = EIREPORT
        self.check_ei_response(oadr_registered_report.eiResponse)
        self.create_or_update_reports(oadr_registered_report.oadrReportRequest)

    def handle_oadr_create_report(self, oadr_create_report):
        """
            Handle an oadrCreateReport request from the VTN.

            The request could have arrived in response to a poll,
            or it could have been part of an oadrRegisteredReport response.

            Create a report for each oadrReportRequest in the list, sending an oadrCreatedReport in response.

        @param oadr_create_report: The VTN's oadrCreateReport request.
        """
        self.oadr_current_service = EIREPORT
        self.oadr_current_request_id = None
        self.create_or_update_reports(oadr_create_report.oadrReportRequest)

    def handle_oadr_updated_report(self, oadr_updated_report):
        """
            The VTN acknowledged receipt of an oadrUpdatedReport, and may have sent a report cancellation.

            Check for report cancellation, and cancel the report if necessary. No need to send a response to the VTN.

        @param oadr_updated_report: The VTN's request.
        """
        self.oadr_current_service = EIREPORT
        self.check_ei_response(oadr_updated_report.eiResponse)
        oadr_cancel_report = oadr_updated_report.oadrCancelReport
        if oadr_cancel_report:
            self.cancel_report(oadr_cancel_report.reportRequestID, acknowledge=False)

    def handle_oadr_cancel_report(self, oadr_cancel_report):
        """
            The VTN responded to an oadrPoll by requesting a report cancellation.

            Respond by canceling the report, then send oadrCanceledReport to the VTN.

        @param oadr_cancel_report: (oadrCancelReportType) The VTN's request.
        """
        self.oadr_current_service = EIREPORT
        self.oadr_current_request_id = oadr_cancel_report.requestID
        self.cancel_report(oadr_cancel_report.reportRequestID, acknowledge=True)

    def handle_oadr_response(self, oadr_response):
        """
            The VTN has acknowledged a VEN request such as oadrCreatedReport.

            No response is needed.

        @param oadr_response: The VTN's request.
        """
        self.check_ei_response(oadr_response.eiResponse)

    def check_ei_response(self, ei_response):
        """
            An eiResponse can appear in multiple kinds of VTN requests.

            If an eiResponse has been received, check for a '200' (OK) response code.
            If any other code is received, the VTN is reporting an error -- log it and raise an exception.

        @param ei_response: (eiResponseType) The VTN's eiResponse.
        """
        self.oadr_current_request_id = ei_response.requestID
        response_code, response_description = OadrResponseExtractor(ei_response=ei_response).extract()
        if response_code != OADR_VALID_RESPONSE:
            error_text = 'Error response from VTN, code={}, description={}'.format(response_code, response_description)
            _log.error(error_text)
            raise OpenADRInternalException(error_text, response_code)

    def create_or_update_reports(self, report_list):
        """
            Process report creation/update requests from the VTN (which could have arrived in different payloads).

            The requests could have arrived in response to a poll,
            or they could have been part of an oadrRegisteredReport response.

            Create/Update reports, and publish info about them on the volttron message bus.
            Send an oadrCreatedReport response to the VTN for each report.

        @param report_list: A list of oadrReportRequest. Can be None.
        """

        def create_temp_rpt(report_request):
            """Validate the report request, creating a temporary EiReport instance in the process."""
            extractor = OadrReportExtractor(request=report_request)
            tmp_report = EiReport(None,
                                  extractor.extract_report_request_id(),
                                  extractor.extract_specifier_id())
            rpt_params = self.report_parameters.get(tmp_report.report_specifier_id, None)
            if rpt_params is None:
                err_msg = 'No parameters found for report with specifier ID {}'.format(tmp_report.report_specifier_id)
                _log.error(err_msg)
                raise OpenADRInterfaceException(err_msg, OADR_BAD_DATA)
            extractor.report_parameters = rpt_params
            extractor.report = tmp_report
            extractor.extract_report()
            return tmp_report

        def update_rpt(tmp_rpt, rpt):
            """If the report changed, update its parameters in the database, and publish them on the message bus."""
            if rpt.report_specifier_id != tmp_rpt.report_specifier_id \
                    or rpt.start_time != tmp_rpt.start_time \
                    or rpt.end_time != tmp_rpt.end_time \
                    or rpt.interval_secs != tmp_rpt.interval_secs:
                rpt.copy_from_report(tmp_rpt)
                self.commit()
                self.publish_telemetry_parameters_for_report(rpt)

        def create_rpt(tmp_rpt):
            """Store the new report request in the database, and publish it on the message bus."""
            self.add_report(tmp_rpt)
            self.publish_telemetry_parameters_for_report(tmp_rpt)

        def cancel_rpt(rpt):
            """A report cancellation was received. Process it and notify interested parties."""
            rpt.status = rpt.STATUS_CANCELED
            self.commit()
            self.publish_telemetry_parameters_for_report(rpt)

        oadr_report_request_ids = []

        try:
            if report_list:
                for oadr_report_request in report_list:
                    temp_report = create_temp_rpt(oadr_report_request)
                    existing_report = self.get_report_for_report_request_id(temp_report.report_request_id)
                    if temp_report.status == temp_report.STATUS_CANCELED:
                        if existing_report:
                            oadr_report_request_ids.append(temp_report.report_request_id)
                            cancel_rpt(existing_report)
                            self.send_oadr_created_report(oadr_report_request)
                        else:
                            # Received notification of a new report, but it's already canceled. Take no action.
                            pass
                    else:
                        oadr_report_request_ids.append(temp_report.report_request_id)
                        if temp_report.report_specifier_id == 'METADATA':
                            # Rule 301/327: If the request's specifierID is 'METADATA', send an oadrRegisterReport.
                            self.send_oadr_created_report(oadr_report_request)
                            self.send_oadr_register_report()
                        elif existing_report:
                            update_rpt(temp_report, existing_report)
                            self.send_oadr_created_report(oadr_report_request)
                        else:
                            create_rpt(temp_report)
                            self.send_oadr_created_report(oadr_report_request)
        except OpenADRInterfaceException, err:
            # If a VTN message contains a mix of valid and invalid reports, respond to the valid ones.
            # Don't reject the entire message due to an invalid report.
            _log.warning('Report error: {}'.format(err), exc_info=True)
            self.send_oadr_response(err.message, err.error_code or OADR_BAD_DATA)
        except Exception, err:
            _log.warning('Unanticipated error during report processing: {}'.format(err), exc_info=True)
            self.send_oadr_response(err.message, OADR_BAD_DATA)

        all_active_reports = self._get_reports()
        for agent_report in all_active_reports:
            if agent_report.report_request_id not in oadr_report_request_ids:
                # If the VTN's request omitted an active report, treat it as an implied cancellation.
                report_request_id = agent_report.report_request_id
                _log.debug('Report request ID {} not sent by VTN, canceling the report.'.format(report_request_id))
                self.cancel_report(report_request_id, acknowledge=True)

    def cancel_report(self, report_request_id, acknowledge=False):
        """
            The VTN asked to cancel a report, in response to either report telemetry or an oadrPoll. Cancel it.

        @param report_request_id: (string) The report_request_id of the report to be canceled.
        @param acknowledge: (boolean) If True, send an oadrCanceledReport acknowledgment to the VTN.
        """
        if report_request_id is None:
            raise OpenADRInterfaceException('Missing oadrCancelReport.reportRequestID', OADR_BAD_DATA)
        report = self.get_report_for_report_request_id(report_request_id)
        if report:
            report.status = report.STATUS_CANCELED
            self.commit()
            self.publish_telemetry_parameters_for_report(report)
            if acknowledge:
                self.send_oadr_canceled_report(report_request_id)
        else:
            # The VEN got asked to cancel a report that it doesn't have. Do nothing.
            pass

    # ***************** Send Requests from the VEN to the VTN ********************

    def send_oadr_poll(self):
        """Send oadrPoll to the VTN."""
        _log.debug('VEN: oadrPoll')
        self.oadr_current_service = POLL
        # OADR rule 37: The VEN must support the PULL implementation.
        self._last_poll = utils.get_aware_utc_now()
        self.send_vtn_request('oadrPoll', OadrPollBuilder(ven_id=self.ven_id).build())

    def send_oadr_query_registration(self):
        """Send oadrQueryRegistration to the VTN."""
        _log.debug('VEN: oadrQueryRegistration')
        self.oadr_current_service = EIREGISTERPARTY
        self.send_vtn_request('oadrQueryRegistration', OadrQueryRegistrationBuilder().build())

    def send_oadr_create_party_registration(self):
        """Send oadrCreatePartyRegistration to the VTN."""
        _log.debug('VEN: oadrCreatePartyRegistration')
        self.oadr_current_service = EIREGISTERPARTY
        send_signature = (self.security_level == 'high')
        # OADR rule 404: If the VEN hasn't registered before, venID and registrationID should be empty.
        builder = OadrCreatePartyRegistrationBuilder(ven_id=None, xml_signature=send_signature, ven_name=self.ven_name)
        self.send_vtn_request('oadrCreatePartyRegistration', builder.build())

    def send_oadr_request_event(self):
        """Send oadrRequestEvent to the VTN."""
        _log.debug('VEN: oadrRequestEvent')
        self.oadr_current_service = EIEVENT
        self.send_vtn_request('oadrRequestEvent', OadrRequestEventBuilder(ven_id=self.ven_id).build())

    def send_oadr_created_event(self, event, error_code=None, error_message=None):
        """
            Send oadrCreatedEvent to the VTN.

        @param event: (EiEvent) The event that is the subject of the request.
        @param error_code: (string) eventResponse error code. Used when reporting event protocol errors.
        @param error_message: (string) eventResponse error message. Used when reporting event protocol errors.
        """
        _log.debug('VEN: oadrCreatedEvent')
        self.oadr_current_service = EIEVENT
        builder = OadrCreatedEventBuilder(event=event, ven_id=self.ven_id,
                                          error_code=error_code, error_message=error_message)
        self.send_vtn_request('oadrCreatedEvent', builder.build())

    def send_oadr_register_report(self):
        """
            Send oadrRegisterReport (METADATA) to the VTN.

            Sample oadrRegisterReport from the OpenADR Program Guide:

                <oadr:oadrRegisterReport ei:schemaVersion="2.0b">
                    <pyld:requestID>RegReq120615_122508_975</pyld:requestID>
                    <oadr:oadrReport>
                        --- See oadr_report() ---
                    </oadr:oadrReport>
                    <ei:venID>ec27de207837e1048fd3</ei:venID>
                </oadr:oadrRegisterReport>
        """
        _log.debug('VEN: oadrRegisterReport')
        self.oadr_current_service = EIREPORT
        # The VEN is currently hard-coded to support the 'telemetry' report, which sends baseline and measured power,
        # and the 'telemetry_status' report, which sends online and manual_override status.
        # In order to support additional reports and telemetry types, the VEN would need to store other data elements
        # as additional columns in its SQLite database.
        builder = OadrRegisterReportBuilder(reports=self.metadata_reports(), ven_id=self.ven_id)
        # The EPRI VTN server responds to this request with "452: Invalid ID". Why?
        self.send_vtn_request('oadrRegisterReport', builder.build())

    def send_oadr_update_report(self, report):
        """
            Send oadrUpdateReport to the VTN.

            Sample oadrUpdateReport from the OpenADR Program Guide:

                <oadr:oadrUpdateReport ei:schemaVersion="2.0b">
                    <pyld:requestID>ReportUpdReqID130615_192730_445</pyld:requestID>
                    <oadr:oadrReport>
                        --- See OadrUpdateReportBuilder ---
                    </oadr:oadrReport>
                    <ei:venID>VEN130615_192312_582</ei:venID>
                </oadr:oadrUpdateReport>

        @param report: (EiReport) The report for which telemetry should be sent.
        """
        _log.debug('VEN: oadrUpdateReport (report {})'.format(report.report_request_id))
        self.oadr_current_service = EIREPORT
        telemetry = self.get_new_telemetry_for_report(report) if report.report_specifier_id == 'telemetry' else []
        builder = OadrUpdateReportBuilder(report=report,
                                          telemetry=telemetry,
                                          online=self.ven_online,
                                          manual_override=self.ven_manual_override,
                                          ven_id=self.ven_id)
        self.send_vtn_request('oadrUpdateReport', builder.build())
        report.last_report = utils.get_aware_utc_now()
        self.commit()

    def send_oadr_created_report(self, report_request):
        """
            Send oadrCreatedReport to the VTN.

        @param report_request: (oadrReportRequestType) The VTN's report request.
        """
        _log.debug('VEN: oadrCreatedReport')
        self.oadr_current_service = EIREPORT
        builder = OadrCreatedReportBuilder(report_request_id=report_request.reportRequestID,
                                           ven_id=self.ven_id,
                                           pending_report_request_ids=self.get_pending_report_request_ids())
        self.send_vtn_request('oadrCreatedReport', builder.build())

    def send_oadr_canceled_report(self, report_request_id):
        """
            Send oadrCanceledReport to the VTN.

        @param report_request_id: (string) The reportRequestID of the report that has been canceled.
        """
        _log.debug('VEN: oadrCanceledReport')
        self.oadr_current_service = EIREPORT
        builder = OadrCanceledReportBuilder(request_id=self.oadr_current_request_id,
                                            report_request_id=report_request_id,
                                            ven_id=self.ven_id,
                                            pending_report_request_ids=self.get_pending_report_request_ids())
        self.send_vtn_request('oadrCanceledReport', builder.build())

    def send_oadr_response(self, response_description, response_code):
        """
            Send an oadrResponse to the VTN.

        @param response_description: (string The response description.
        @param response_code: (string) The response code, 200 if OK.
        """
        _log.debug('VEN: oadrResponse')
        builder = OadrResponseBuilder(response_code=response_code,
                                      response_description=response_description,
                                      request_id=self.oadr_current_request_id or '0',
                                      ven_id=self.ven_id)
        self.send_vtn_request('oadrResponse', builder.build())

    def send_vtn_request(self, request_name, request_object):
        """
            Send a request to the VTN. If the VTN returns a non-empty response, service that request.

            Wrap the request in a SignedObject and then in Payload XML, and post it to the VTN via HTTP.
            If using high security, calculate a digital signature and include it in the request payload.

        @param request_name: (string) The name of the SignedObject attribute where the request is attached.
        @param request_object: (various oadr object types) The request to send.
        """
        signed_object = oadrSignedObject(**{request_name: request_object})
        try:
            # Export the SignedObject as an XML string.
            buff = StringIO.StringIO()
            signed_object.export(buff, 1, pretty_print=True)
            signed_object_xml = buff.getvalue()
        except Exception, err:
            raise OpenADRInterfaceException('Error exporting the SignedObject: {}'.format(err), None)

        if self.security_level == 'high':
            try:
                signature_lxml, signed_object_lxml = self.calculate_signature(signed_object_xml)
            except Exception, err:
                raise OpenADRInterfaceException('Error signing the SignedObject: {}'.format(err), None)
            payload_lxml = self.payload_element(signature_lxml, signed_object_lxml)
            try:
                # Verify that the payload, with signature, is well-formed and can be validated.
                signxml.XMLVerifier().verify(payload_lxml, ca_pem_file=VTN_CA_CERT_FILENAME)
            except Exception, err:
                raise OpenADRInterfaceException('Error verifying the SignedObject: {}'.format(err), None)
        else:
            signed_object_lxml = etree_.fromstring(signed_object_xml)
            payload_lxml = self.payload_element(None, signed_object_lxml)

        if self.log_xml:
            _log.debug('VEN PAYLOAD:')
            _log.debug('\n{}'.format(etree_.tostring(payload_lxml, pretty_print=True)))

        # Post payload XML to the VTN as an HTTP request. Return the VTN's response, if any.
        endpoint = self.vtn_address + (self.oadr_current_service or POLL)
        try:
            payload_xml = etree_.tostring(payload_lxml)
            # OADR rule 53: If simple HTTP mode is used, send the following headers: Host, Content-Length, Content-Type.
            # The EPRI VTN server responds with a 400 "bad request" if a "Host" header is sent.
            _log.debug('Posting VEN request to {}'.format(endpoint))
            response = requests.post(endpoint, data=payload_xml, headers={
                # "Host": endpoint,
                "Content-Length": str(len(payload_xml)),
                "Content-Type": "application/xml"})
            http_code = response.status_code
            if http_code == 200:
                if len(response.content) > 0:
                    self.core.spawn(self.service_vtn_request, response.content)
                else:
                    _log.warning('Received zero-length request from VTN')
            elif http_code == 204:
                # Empty response received. Take no action.
                _log.debug('Empty response received from {}'.format(endpoint))
            else:
                _log.error('Error in http request to {}: response={}'.format(endpoint, http_code), exc_info=True)
                raise OpenADRInterfaceException('Error in VTN request: {}'.format(http_code), None)
        except ConnectionError:
            _log.warning('ConnectionError in http request to {} (is the VTN offline?)'.format(endpoint))
            return None
        except Exception, err:
            raise OpenADRInterfaceException('Error posting OADR XML: {}'.format(err), None)

    # ***************** VOLTTRON RPCs ********************

    @RPC.export
    def respond_to_event(self, event_id, opt_in_choice=None):
        """
            Respond to an event, opting in or opting out.

            If an event's status=unresponded, it is awaiting this call.
            When this RPC is received, the VENAgent sends an eventResponse to
            the VTN, indicating whether optIn or optOut has been chosen.
            If an event remains unresponded for a set period of time,
            it times out and automatically optsIn to the event.

            Since this call causes a change in the event's status, it triggers
            a PubSub call for the event update, as described above.

        @param event_id: (String) ID of an event.
        @param opt_in_choice: (String) 'OptIn' to opt into the event, anything else is treated as 'OptOut'.
        """
        event = self.get_event_for_id(event_id)
        if event:
            if opt_in_choice == event.OPT_TYPE_OPT_IN:
                event.opt_type = opt_in_choice
            else:
                event.opt_type = event.OPT_TYPE_OPT_OUT
            self.commit()
            _log.debug('RPC respond_to_event: Sending {} for event ID {}'.format(event.opt_type, event_id))
            self.send_oadr_created_event(event)
        else:
            raise OpenADRInterfaceException('No event found for event_id {}'.format(event_id), None)

    @RPC.export
    def add_event_for_test(self, event_id, request_id, start_time):
        """Add an event to the database and cache. Used during regression testing only."""
        _log.debug('RPC add_event_for_test: Creating event with ID {}'.format(event_id))
        event = EiEvent(event_id, request_id)
        event.start_time = parser.parse(start_time)
        self.add_event(event)

    @RPC.export
    def get_events(self, **kwargs):
        """
            Return a list of events as a JSON string.

            See _get_eievents() for a list of parameters and a description of method behavior.

            Sample request:
                self.get_events(started_after=utils.get_aware_utc_now() - timedelta(hours=1),
                                end_time_before=utils.get_aware_utc_now())

        @return: (JSON) A list of EiEvents -- see 'PubSub: event update'.
        """
        _log.debug('RPC get_events')
        events = self._get_events(**kwargs)
        return None if events is None else self.json_object([e.as_json_compatible_object() for e in events])

    @RPC.export
    def get_telemetry_parameters(self):
        """
            Return the VENAgent's current set of telemetry parameters.

        @return: (JSON) Current telemetry parameters -- see 'PubSub: telemetry parameters update'.
        """
        _log.debug('RPC get_telemetry_parameters')
        # If there is an active report, return its telemetry parameters.
        # Otherwise return the telemetry report parameters in agent config.
        rpts = self.active_reports()
        report = rpts[0] if len(rpts) > 0 else self.metadata_report('telemetry')
        # Extend what's reported to include parameters other than just telemetry parameters.
        return {'online': self.ven_online,
                'manual_override': self.ven_manual_override,
                'telemetry': report.telemetry_parameters,
                'report parameters': self.json_object(report.as_json_compatible_object())}

    @RPC.export
    def set_telemetry_status(self, online, manual_override):
        """
            Update the VENAgent's reporting status.

            To be compliant with the OADR profile spec, set these properties to either 'TRUE' or 'FALSE'.

        @param online: (Boolean) Whether the VENAgent's resource is online.
        @param manual_override: (Boolean) Whether resource control has been overridden.
        """
        _log.debug('RPC set_telemetry_status: online={}, manual_override={}'.format(online, manual_override))
        # OADR rule 510: Provide a TELEMETRY_STATUS report that includes oadrOnline and oadrManualOverride values.
        self.ven_online = online
        self.ven_manual_override = manual_override

    @RPC.export
    def report_telemetry(self, telemetry):
        """
            Receive an update of the VENAgent's report metrics, and store them in the agent's database.

            Examples of telemetry are:
            {
                'baseline_power_kw': '15.2',
                'current_power_kw': '371.1',
                'start_time': '2017-11-21T23:41:46.051405',
                'end_time': '2017-11-21T23:42:45.951405'
            }

        @param telemetry: (JSON) Current value of each report metric, with reporting-interval start/end timestamps.
        """
        _log.debug('RPC report_telemetry: {}'.format(telemetry))
        baseline_power_kw = telemetry.get('baseline_power_kw')
        current_power_kw = telemetry.get('current_power_kw')
        start_time = utils.parse_timestamp_string(telemetry.get('start_time'))
        end_time = utils.parse_timestamp_string(telemetry.get('end_time'))
        for report in self.active_reports():
            self.add_telemetry(EiTelemetryValues(report_request_id=report.report_request_id,
                                                 baseline_power_kw=baseline_power_kw,
                                                 current_power_kw=current_power_kw,
                                                 start_time=start_time,
                                                 end_time=end_time))

    # ***************** VOLTTRON Pub/Sub Requests ********************

    def publish_event(self, an_event):
        """
            Publish an event.

            When an event is created/updated, it is published to the VOLTTRON bus
            with a topic that includes 'openadr/event_update'.

            Event JSON structure:
                {
                    "event_id"      : String,
                    "creation_time" : DateTime,
                    "start_time"    : DateTime,
                    "end_time"      : DateTime or None,
                    "priority"      : Integer,    # Values: 0, 1, 2, 3. Usually expected to be 1.
                    "signals"       : String,     # Values: json string describing one or more signals.
                    "status"        : String,     # Values: unresponded, far, near, active,
                                                  #         completed, canceled.
                    "opt_type"      : String      # Values: optIn, optOut, none.
                }

            If an event status is 'unresponded', the VEN agent is awaiting a decision on
            whether to optIn or optOut. The downstream agent that subscribes to this PubSub
            message should communicate that choice to the VEN agent by calling respond_to_event()
            (see below). The VEN agent then relays the choice to the VTN.

        @param an_event: an EiEvent.
        """
        if an_event.test_event != 'false':
            # OADR rule 6: If testEvent is present and != "false", handle the event as a test event.
            _log.debug('Suppressing publication of test event {}'.format(an_event))
        else:
            _log.debug('Publishing event {}'.format(an_event))
            request_headers = {headers.TIMESTAMP: format_timestamp(utils.get_aware_utc_now())}
            self.vip.pubsub.publish(peer='pubsub',
                                    topic=topics.OPENADR_EVENT,
                                    headers=request_headers,
                                    message=self.json_object(an_event.as_json_compatible_object()))

    def publish_telemetry_parameters_for_report(self, report):
        """
            Publish telemetry parameters.

            When the VEN agent telemetry reporting parameters have been updated (by the VTN),
            they are published with a topic that includes 'openadr/telemetry_parameters'.
            If a particular report has been updated, the reported parameters are for that report.

            Telemetry parameters JSON example:
            {
                "telemetry": {
                    "baseline_power_kw": {
                        "r_id": "baseline_power",
                        "min_frequency": "30",
                        "max_frequency": "60",
                        "report_type": "baseline",
                        "reading_type": "Direct Read",
                        "units": "powerReal",
                        "method_name": "get_baseline_power"
                    }
                    "current_power_kw": {
                        "r_id": "actual_power",
                        "min_frequency": "30",
                        "max_frequency": "60",
                        "report_type": "reading",
                        "reading_type": "Direct Read",
                        "units": "powerReal",
                        "method_name": "get_current_power"
                    }
                    "manual_override": "False",
                    "report_status": "active",
                    "online": "False",
                }
            }

            The above example indicates that, for reporting purposes, telemetry values
            for baseline_power and actual_power should be updated -- via report_telemetry() -- at
            least once every 30 seconds.

            Telemetry value definitions such as baseline_power and actual_power come from the
            agent configuration.

        @param report: (EiReport) The report whose parameters should be published.
        """
        _log.debug('Publishing telemetry parameters')
        request_headers = {headers.TIMESTAMP: format_timestamp(utils.get_aware_utc_now())}
        self.vip.pubsub.publish(peer='pubsub',
                                topic=topics.OPENADR_STATUS,
                                headers=request_headers,
                                message=report.telemetry_parameters)

    # ***************** Database Requests ********************

    def active_events(self):
        """Return a list of events that are neither COMPLETED nor CANCELED."""
        return self._get_events()

    def get_event_for_id(self, event_id):
        """Return the event with ID event_id, or None if not found."""
        event_list = self._get_events(event_id=event_id, in_progress_only=False)
        return event_list[0] if len(event_list) == 1 else None

    def _get_events(self, event_id=None, in_progress_only=True, started_after=None, end_time_before=None):
        """
            Return a list of EiEvents. (internal method)

            By default, return only event requests with status=active or status=unresponded.

            If an event's status=active, a DR event is currently in progress.

        @param event_id: (String) Default None.
        @param in_progress_only: (Boolean) Default True.
        @param started_after: (DateTime) Default None.
        @param end_time_before: (DateTime) Default None.
        @return: A list of EiEvents.
        """
        # For requests by event ID, query the cache first before querying the database.
        if event_id:
            event = self._active_events.get(event_id, None)
            if event:
                return [event]

        db_event = globals()['EiEvent']
        events = self.get_db_session().query(db_event)
        if event_id is not None:
            events = events.filter(db_event.event_id == event_id)
        if in_progress_only:
            events = events.filter(~db_event.status.in_([EiEvent.STATUS_COMPLETED, EiEvent.STATUS_CANCELED]))
        if started_after:
            events = events.filter(db_event.start_time > started_after)
        if end_time_before and db_event.end_time:
            # An event's end_time can be None, indicating that it doesn't expire until Canceled.
            # If the event's end_time is None, don't apply this filter to it.
            events = events.filter(db_event.end_time < end_time_before)
        return events.all()

    def add_event(self, event):
        """A new event has been created. Add it to the event cache, and also to the database."""
        self._active_events[event.event_id] = event
        self.get_db_session().add(event)
        self.commit()

    def set_event_status(self, event, status):
        _log.debug('Transitioning status to {} for event ID {}'.format(status, event.event_id))
        event.status = status
        self.commit()

    def expire_event(self, event):
        """Remove the event from the event cache. (It remains in the SQLite database.)"""
        self._active_events.pop(event.event_id)

    def active_reports(self):
        """Return a list of reports that are neither COMPLETED nor CANCELED."""
        return self._get_reports()

    def add_report(self, report):
        """A new report has been created. Add it to the report cache, and also to the database."""
        self._active_reports[report.report_request_id] = report
        self.get_db_session().add(report)
        self.commit()

    def set_report_status(self, report, status):
        _log.debug('Transitioning status to {} for report request ID {}'.format(status, report.report_request_id))
        report.status = status
        self.commit()

    def expire_report(self, report):
        """Remove the report from the report cache. (It remains in the SQLite database.)"""
        self._active_reports.pop(report.report_request_id)

    def get_report_for_report_request_id(self, report_request_id):
        """Return the EiReport with request ID report_request_id, or None if not found."""
        report_list = self._get_reports(report_request_id=report_request_id, active_only=False)
        return report_list[0] if len(report_list) == 1 else None

    def get_reports_for_report_specifier_id(self, report_specifier_id):
        """Return the EiReport with request ID report_request_id, or None if not found."""
        return self._get_reports(report_specifier_id=report_specifier_id, active_only=True)

    def get_pending_report_request_ids(self):
        """Return a list of reportRequestIDs for each active report."""
        # OpenADR rule 329: Include all current report request IDs in the oadrPendingReports list.
        return [r.report_request_id for r in self._get_reports()]

    def _get_reports(self, report_request_id=None, report_specifier_id=None, active_only=True,
                     started_after=None, end_time_before=None):
        """
            Return a list of EiReport.

            By default, return only report requests with status=active.

        @param report_request_id: (String) Default None.
        @param report_specifier_id: (String) Default None.
        @param active_only: (Boolean) Default True.
        @param started_after: (DateTime) Default None.
        @param end_time_before: (DateTime) Default None.
        @return: A list of EiReports.
        """
        # For requests by report ID, query the cache first before querying the database.
        if report_request_id:
            report = self._active_reports.get(report_request_id, None)
            if report:
                return [report]

        db_report = globals()['EiReport']
        reports = self.get_db_session().query(db_report)
        if report_request_id is not None:
            reports = reports.filter(db_report.report_request_id == report_request_id)
        if report_specifier_id is not None:
            reports = reports.filter(db_report.report_specifier_id == report_specifier_id)
        if active_only:
            reports = reports.filter(~db_report.status.in_([EiReport.STATUS_COMPLETED, EiReport.STATUS_CANCELED]))
        if started_after:
            reports = reports.filter(db_report.start_time > started_after)
        if end_time_before and db_report.end_time:
            # A report's end_time can be None, indicating that it doesn't expire until Canceled.
            # If the report's end_time is None, don't apply this filter to it.
            reports = reports.filter(db_report.end_time < end_time_before)
        return reports.all()

    def metadata_reports(self):
        """Return an EiReport instance containing telemetry metadata for each report definition in agent config."""
        return [self.metadata_report(rpt_name) for rpt_name in self.report_parameters.keys()]

    def metadata_report(self, specifier_id):
        """Return an EiReport instance for the indicated specifier_id, or None if its' not in agent config."""
        params = self.report_parameters.get(specifier_id, None)
        report = EiReport('', '', specifier_id)             # No requestID, no reportRequestID
        report.name = params.get('report_name_metadata', None)
        try:
            interval_secs = int(params.get('report_interval_secs_default', None))
        except ValueError:
            error_msg = 'Default report interval {} is not an integer number of seconds'.format(default)
            raise OpenADRInternalException(error_msg, OADR_BAD_DATA)
        report.interval_secs = interval_secs
        report.telemetry_parameters = json.dumps(params.get('telemetry_parameters', None))
        report.report_specifier_id = specifier_id
        report.status = report.STATUS_INACTIVE
        return report

    def get_new_telemetry_for_report(self, report):
        """Query for relevant telemetry that's arrived since the report was last sent to the VTN."""
        db_telemetry_values = globals()['EiTelemetryValues']
        telemetry = self.get_db_session().query(db_telemetry_values)
        telemetry = telemetry.filter(db_telemetry_values.report_request_id == report.report_request_id)
        telemetry = telemetry.filter(db_telemetry_values.created_on > report.last_report)
        return telemetry.all()

    def add_telemetry(self, telemetry):
        """New telemetry has been received. Add it to the database."""
        self.get_db_session().add(telemetry)
        self.commit()

    def telemetry_cleanup(self):
        """gevent thread for periodically deleting week-old telemetry from the database."""
        db_telemetry_values = globals()['EiTelemetryValues']
        telemetry = self.get_db_session().query(db_telemetry_values)
        total_rows = telemetry.count()
        telemetry = telemetry.filter(db_telemetry_values.created_on < utils.get_aware_utc_now() - timedelta(days=7))
        deleted_row_count = telemetry.delete()
        if deleted_row_count:
            _log.debug('Deleting {} outdated of {} total telemetry rows in db'.format(deleted_row_count, total_rows))
        self.commit()

    def commit(self):
        """Flush any modified objects to the SQLite database."""
        self.get_db_session().commit()

    def get_db_session(self):
        """Return the SQLite database session. Initialize the session if this is the first time in."""
        if not self._db_session:
            # First time: create a SQLAlchemy engine and session.
            try:
                database_dir = os.path.dirname(self.db_path)
                if not os.path.exists(database_dir):
                    _log.debug('Creating sqlite database directory {}'.format(database_dir))
                    os.makedirs(database_dir)
                engine_path = 'sqlite:///' + self.db_path
                _log.debug('Connecting to sqlite database {}'.format(engine_path))
                engine = create_engine(engine_path).connect()
                ORMBase.metadata.create_all(engine)
                self._db_session = sessionmaker(bind=engine)()
            except AttributeError, err:
                error_msg = 'Unable to open sqlite database named {}: {}'.format(self.db_path, err)
                raise OpenADRInterfaceException(error_msg, None)
        return self._db_session

    # ***************** Utility Methods ********************

    @staticmethod
    def payload_element(signature_lxml, signed_object_lxml):
        """
            Construct and return an XML element for Payload.

            Append a child Signature element if one is provided.
            Append a child SignedObject element.

        @param signature_lxml: (Element or None) Signature element.
        @param signed_object_lxml: (Element) SignedObject element.
        @return: (Element) Payload element.
        """
        payload = etree_.Element("{http://openadr.org/oadr-2.0b/2012/07}oadrPayload",
                                 nsmap=signed_object_lxml.nsmap)
        if signature_lxml:
            payload.append(signature_lxml)
        payload.append(signed_object_lxml)
        return payload

    @staticmethod
    def calculate_signature(signed_object_xml):
        """
            Calculate a digital signature for the SignedObject to be sent to the VTN.

        @param signed_object_xml: (xml string) A SignedObject.
        @return: (lxml) A Signature and a SignedObject.
        """
        signed_object_lxml = etree_.fromstring(signed_object_xml)
        signed_object_lxml.set('Id', 'signedObject')
        # Use XMLSigner to create a Signature.
        # Use "detached method": the signature lives alonside the signed object in the XML element tree.
        # Use c14n "exclusive canonicalization": the signature is independent of namespace inclusion/exclusion.
        signer = signxml.XMLSigner(method=signxml.methods.detached,
                                   c14n_algorithm='http://www.w3.org/2001/10/xml-exc-c14n#')
        signature_lxml = signer.sign(signed_object_lxml,
                                     key=open(KEY_FILENAME, 'rb').read(),
                                     cert=open(CERT_FILENAME, 'rb').read(),
                                     key_name='123')
        # This generated Signature lacks the ReplayProtect property described in OpenADR profile spec section 10.6.3.
        return signature_lxml, signed_object_lxml

    def register_endpoints(self):
        """
            Register each endpoint URL and its callback.

            These endpoint definitions are used only by "PUSH" style VTN communications,
            not by responses to VEN polls.
        """
        _log.debug("Registering Endpoints: {}".format(self.__class__.__name__))
        for endpoint in OPENADR_ENDPOINTS.itervalues():
            self.vip.web.register_endpoint(endpoint.url, getattr(self, endpoint.callback), "raw")

    def json_object(self, obj):
        """Ensure that an object is valid JSON by dumping it with json_converter and then reloading it."""
        obj_string = json.dumps(obj, default=self.json_converter)
        obj_json = json.loads(obj_string)
        return obj_json

    @staticmethod
    def json_converter(object_to_dump):
        """When calling json.dumps, convert datetime instances to strings."""
        if isinstance(object_to_dump, dt):
            return object_to_dump.__str__()


def main():
    """Start the agent."""
    utils.vip_main(ven_agent, identity='venagent', version=__version__)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
