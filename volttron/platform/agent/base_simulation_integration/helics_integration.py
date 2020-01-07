# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:
#
# Copyright 2019, Battelle Memorial Institute.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# This material was prepared as an account of work sponsored by an agency of
# the United States Government. Neither the United States Government nor the
# United States Department of Energy, nor Battelle, nor any of their
# employees, nor any jurisdiction or organization that has cooperated in the
# development of these materials, makes any warranty, express or
# implied, or assumes any legal liability or responsibility for the accuracy,
# completeness, or usefulness or any information, apparatus, product,
# software, or process disclosed, or represents that its use would not infringe
# privately owned rights. Reference herein to any specific commercial product,
# process, or service by trade name, trademark, manufacturer, or otherwise
# does not necessarily constitute or imply its endorsement, recommendation, or
# favoring by the United States Government or any agency thereof, or
# Battelle Memorial Institute. The views and opinions of authors expressed
# herein do not necessarily state or reflect those of the
# United States Government or any agency thereof.
#
# PACIFIC NORTHWEST NATIONAL LABORATORY operated by
# BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
# under Contract DE-AC05-76RL01830
# }}}

try:
    import helics as h
    HAS_HELICS = True
except ImportError:
    HAS_HELICS = False
    RuntimeError('HELICS must be installed before running this script ')

import os
import logging
import gevent
import weakref
from volttron.platform.agent.base_simulation_integration import BaseSimIntegration
from volttron.platform import jsonapi

_log = logging.getLogger(__name__)
__version__ = '1.0'


class HELICSSimIntegration(BaseSimIntegration):
    """
    The class is responsible for integration with HELICS co-simulation platform
    """
    def __init__(self, config, pubsub):
        super(HELICSSimIntegration, self).__init__(config)
        self.pubsub = weakref.ref(pubsub)
        self.fed = None
        self._work_callback = None
        self._simulation_started = False
        self._simulation_complete = False
        self._simulation_delta = self.config.get('timedelta', 1.0) #seconds
        self._simulation_length = self.config.get('simulation_length', 3600) #seconds
        self.current_time = 0
        self.inputs = []
        self.outputs = []
        self.endpoints = {}
        self.current_values = {}
        self.helics_to_volttron_publish = {}

    def register_inputs(self, config=None, callback=None):
        """
        Register configuration parameters with HELICS. The config parameters may include
        but not limited to:
        1. Name of the federate
        2. simulation length
        2. Type of core to use (zmq/tcp/udp etc)
        3. list (and type) of subscriptions
        4. list (and type) of publications
        5. broker address (if not default)
        :param config: config parameters
        :param callback: Register agent callback method
        :return:
        """
        self._work_callback = callback
        # strip volttron topics from input config
        config.pop('volttron_subscriptions', None)
        subscriptions = config.get('subscriptions', [])

        for sub in subscriptions:
            volttron_topic = sub.pop('volttron_topic', None)
            if volttron_topic is not None:
                self.helics_to_volttron_publish[sub.get('key')] = volttron_topic
        publications = config.get('publications', [])
        for pub in publications:
            volttron_topic = pub.pop('volttron_topic', None)

        # Create a temporary json file
        tmp_file = os.path.join(os.getcwd(), 'fed_cfg.json')
        _log.debug("tmp file: {}".format(tmp_file))
        with open(tmp_file, 'w') as fout:
            fout.write(jsonapi.dumps(config))
        _log.debug("Create Combination Federate")
        # Create federate from provided config parameters
        self.fed = h.helicsCreateCombinationFederateFromConfig(tmp_file)
        federate_name = h.helicsFederateGetName(self.fed)
        _log.debug("Federate name: {}".format(federate_name))

        endpoint_count = h.helicsFederateGetEndpointCount(self.fed)
        _log.debug("Endpoint count: {}".format(endpoint_count))
        subkeys_count = h.helicsFederateGetInputCount(self.fed)
        _log.debug("Subscription key count: {}".format(subkeys_count))
        pubkeys_count = h.helicsFederateGetPublicationCount(self.fed)
        _log.debug("Publication key count: {}".format(endpoint_count))

        for i in range(0, endpoint_count):
            endpt_idx = h.helicsFederateGetEndpointByIndex(self.fed, i)
            endpt_name = h.helicsEndpointGetName(endpt_idx)
            self.endpoints[endpt_name] = endpt_idx

        for i in range(0, subkeys_count):
            inputs = dict()
            idx = h.helicsFederateGetInputByIndex(self.fed, i)
            inputs['sub_id'] = idx
            inputs['type'] = h.helicsInputGetType(idx)
            inputs['key'] = h.helicsSubscriptionGetKey(idx)
            self.inputs.append(inputs)
            data = dict(type=inputs['type'], value=None)

        for i in range(0, pubkeys_count):
            outputs = dict()
            idx = h.helicsFederateGetPublicationByIndex(self.fed, i)
            outputs['pub_id'] = idx
            outputs['type'] = h.helicsPublicationGetType(idx)
            outputs['key'] = h.helicsPublicationGetKey(idx)
            self.outputs.append(outputs)
            data = dict(type=outputs['type'], value=None)

    def start_simulation(self):
        """
        This is a blocking call until the all the federates get connected to HELICS broker
        :return:
        """
        _log.debug("############ Entering Execution Mode ##############")
        h.helicsFederateEnterExecutingMode(self.fed)
        _log.debug("Spawning simulation loop to HELICS events")
        gevent.spawn(self._sim_loop)
        # Allow the spawned greenlet to run.
        gevent.sleep(0.1)

    def _sim_loop(self):
        """
        Continuous loop to get registered input values from HELICS and feed it to user callback
        :return:
        """
        _log.info("Starting simulation loop")
        self._simulation_started = True
        while self.current_time < self._simulation_length:
            for in_put in self.inputs:
                sub_key = in_put['key']
                # NOTE: Values are persisted in HELICS. Old values are returned if they dont
                # get updated in current timestep
                self.current_values[sub_key] = self._get_input_based_on_type(in_put)
                try:
                    # Get volttron topic for the input key
                    volttron_topic = self.helics_to_volttron_publish[sub_key]
                    if self.pubsub is not None:
                        self.pubsub.publish('pubsub', topic=volttron_topic, message=self.current_values[sub_key])
                except KeyError:
                    # No VOLTTRON topic for input key
                    pass

            # Call user provided callback to perform work on HELICS inputs
            self._work_callback()
            # This allows other event loops to run
            gevent.sleep(0.000000001)
        _log.debug("Simulation completed. Closing connection to HELICS")
        # Check if anything to publish
        self._simulation_complete = True
        # Closing connection to HELICS
        self.stop_simulation()

    def publish_to_simulation(self, topic, message):
        """
        Publish message on HELICS bus
        :param topic: HELICS publication key
        :param message: message
        :return:
        """
        try:
            info = self.outputs[topic]
            info['value'] = message
            self._publish_based_on_type(info)
        except KeyError as e:
            _log.error("Unknown publication key {}".format(topic))

    def _publish_based_on_type(self, output):
        """
        Publish message based on type
        :param output:
        :return:
        """
        if output['type'] == 'integer':
            h.helicsPublicationPublishInteger(output['pub_id'], output['value'])
        elif output['type'] == 'double':
            h.helicsPublicationPublishDouble(output['pub_id'], output['value'])
        elif output['type'] == 'string':
            h.helicsPublicationPublishString(output['pub_id'], output['value'])
        elif output['type'] == 'complex':
            h.helicsPublicationPublishComplex(output['pub_id'], output['value'])
        elif output['type'] == 'vector':
            h.helicsPublicationPublishVector(output['pub_id'], output['value'])
        elif output['type'] == 'boolean':
            h.helicsPublicationPublishBoolean(output['pub_id'], output['value'])
        else:
            _log.error("Unknown datatype: {}".format(output['type']))

    def _get_input_based_on_type(self, in_put):
        """
        Get input based on type
        :param in_put:
        :return:
        """
        val = None
        sub_id = in_put['sub_id']
        if in_put['type'] == 'integer':
            val = h.helicsInputGetInteger(sub_id)
        elif in_put['type'] == 'double':
            val = h.helicsInputGetDouble(sub_id)
        elif in_put['type'] == 'string':
            val = h.helicsInputGetString(sub_id)
        elif in_put['type'] == 'complex':
            real, imag = h.helicsInputGetComplex(sub_id)
            val = [real, imag]
        elif in_put['type'] == 'vector':
            val = h.helicsInputGetVector(sub_id)
        elif in_put['type'] == 'boolean':
            val = h.helicsInputGetBoolean(sub_id)
        else:
            _log.error("Unknown datatype: {}".format(in_put['type']))
        return val

    def make_blocking_time_request(self, time_request=None):
        """
        This is a blocking call till the next time request is granted
        :param time_request:
        :return:
        """
        if time_request is None:
            time_request = self.current_time + self._simulation_delta
        granted_time = -1
        while granted_time < time_request:
            granted_time = h.helicsFederateRequestTime(self.fed, time_request)
            gevent.sleep(0.000000001)
        _log.debug("GRANTED TIME: {}".format(granted_time))
        self.current_time = granted_time

    def make_time_request(self, time_request=None):
        """
        Request for next time step. Granted time maybe lower than the requested time
        :param time_request:
        :return:
        """
        if time_request is None:
            time_request = self.current_time + self._simulation_delta
        granted_time = h.helicsFederateRequestTime(self.fed, time_request)
        _log.debug("GRANTED TIME maybe lower than time requested: {}".format(granted_time))
        self.current_time = granted_time

    def pause_simulation(self):
        pass

    def resume_simulation(self):
        pass

    def is_sim_installed(self):
        return HAS_HELICS

    def send_to_endpoint(self, endpoint_name, destination='', value=0):
        """
        Send the message to specific endpoint
        :param endpoint_name: endpoint name
        :param destination: destination name if any
        :param value: message
        :return:
        """
        endpoint_idx = self.endpoints[endpoint_name]
        h.helicsEndpointSendMessageRaw(endpoint_idx, destination, str(value))

    def stop_simulation(self):
        """
        Disconnect the federate from helics core and close the library
        :return:
        """
        h.helicsFederateFinalize(self.fed)
        h.helicsFederateFree(self.fed)
        h.helicsCloseLibrary()


if __name__ == "__main__":
    hobj = HELICSSimIntegration('test')
    hobj.register_inputs('test', 'callback')
    hobj.start_simulation()
