
#MESSAGE = [('arc/tstat/Manual Operation',1),('arc/tstat/Manual Occupied Cool Setpoint',78), ('arc/tstat/Manual Occupied Heat Setpoint',75),
#          ('arc/tstat/HVAC Type Setting',1),
#          ('arc/tstat/Heat Type Setting',0),
#('arc/tstat/HVAC Cooling Stages Setting',2),
#('arc/tstat/HVAC Heating Stages Setting',2)]
# occupancy input not writable
# Probably want mode to be 4 for auto
MESSAGE = [('hptes/modbus/Supervisor_CallCold',False),('hptes/modbus/Supervisor_ChargeCold',False),('hptes/modbus/Supervisor_ChargeHot',False)]
#MESSAGE = [('hptes/modbus/xCommandOn',int(1))]
#MESSAGE = [('arc/tstat/System Mode',4)]
#Message = [('arc/tstat/Manual Unoccupied Heat Setpoint', 66)]
DEVICES = ['devices/arc/tstat']
DEVICES = ['devices/hptes/modbus'] # may need to add devices to beginning of this, not sure
__docformat__ = 'reStructuredText'

import logging
import sys
import pandas as pd
import yaml
import json
from pytz import timezone
from datetime import datetime, timedelta, time
from volttron.platform.agent.utils import format_timestamp, get_aware_utc_now
from volttron.platform.messaging import headers as headers_mod
from volttron.platform.agent import utils
from volttron.platform.vip.agent import Agent, Core, RPC
from volttron.platform.scheduling import cron
import random
from time import sleep

_log = logging.getLogger(__name__)
utils.setup_logging()
__version__ = "0.1"

REQUESTER_ID = 'requester_id'
TASK_ID = 'task_id'

def interfaceagent(config_path, **kwargs):

    _log.debug("Config path: {}".format(config_path))
    try:
        config = utils.load_config(config_path)
    except Exception:
        config = {}
    if not config:
        _log.info("Using Agent defaults for starting configuration.")
    _log.debug("config_dict before init: {}".format(config))

    return InterfaceAgent(**kwargs)


class InterfaceAgent(Agent):
    """
    Agent used to test the functionality of the CSV driver
    """

    def __init__(self, **kwargs):
        # Configure the base agent
        super(InterfaceAgent, self).__init__(**kwargs)
        _log.debug("vip_identity: " + self.core.identity)
        self.default_config = {}
        self.vip.config.subscribe(self.configure, actions=["NEW", "UPDATE"])
        self.frequency = 1

    def configure(self, config_name, action, contents):
        """
        Called after the Agent has connected to the message bus.
        If a configuration exists at startup this will be called before onstart

        Is called every time the configuration in the store changes.
        """
        self.config = self.default_config.copy()
        self.config.update(contents)
        self.frequency = 1
        _log.debug("Configuring Agent")
        self.control_runner()

    @Core.receiver('onstart')
    def control_runner(self,sender, **kwargs):
        _log.debug('running controls')

        message = MESSAGE

        _log.debug(message)
        self.actuate(message)
        return
        
        

    def actuate(self,point_setting):
        #will have to schedule all devices
        start = datetime.now()
        end = datetime.now() + timedelta(minutes = self.frequency)
        priority = 'LOW'
        task_id = TASK_ID
        task_id = str(random.randint(0,100000))
        # devices = ['devices/sensibo/FCU1','devices/sensibo/FCU2','devices/sensibo/FCU3','devices/sensibo/FCU4','devices/sensibo/FCU5']
        devices = DEVICES

        msg = [ [device, utils.format_timestamp(start), utils.format_timestamp(end)] for device in devices]
        _log.debug(msg)
        try:
            result = self.vip.rpc.call('platform.actuator',
                                        'request_new_schedule',
                                           REQUESTER_ID,
                                           task_id,
                                           priority,
                                           msg).get(timeout=10)
        except Exception as e:
            _log.error(e)
            _log.warning("Could not contact actuator. Is it running?")
        #_log.info("schedule result {}".format(result))

        print(point_setting)
        result = self.vip.rpc.call('platform.actuator',
                                    'set_multiple_points',
                                    REQUESTER_ID,
                                    point_setting).get(timeout=20)
        print(result)

    def _publish_wrapper(self, topic, headers, message):
        while True:
            try:
                _log.debug("publishing: " + topic)
                self.vip.pubsub.publish('pubsub',
                                        topic,
                                        headers=headers,
                                        message=message).get(timeout=10.0)

                _log.debug("finish publishing: " + topic)
            except gevent.Timeout:
                _log.warning("Did not receive confirmation of publish to "+topic)
                break
            except Again:
                _log.warning("publish delayed: " + topic + " pubsub is busy")
                gevent.sleep(random.random())
            except VIPError as ex:
                _log.warning("driver failed to publish " + topic + ": " + str(ex))
                break
            else:
                break

def main():
    """Main method called to start the agent."""
    utils.vip_main(interfaceagent,
                   version=__version__)


if __name__ == '__main__':
    # Entry point for script
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass

