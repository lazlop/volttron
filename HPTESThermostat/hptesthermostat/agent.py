"""


        MPC run information saved in DB

        Overwrite detection provides 3 hour allowed overwrite on site:

        If most recent point value is different than last written value, an override has occured.
        
        (not relevant to WCEC, we will write occupancy mode via scheudule)
        If the override is accomplished by equipment schedule, it will match an action stored in a table
        This will be saved and ignored

        If override is by human, it will not match stored action. This override will be allowed for some period (maybe 3 hours)

        Function will return a dictionary per point, saying if point is currently overwritten
        {unit name: <True (overwritten), or False <not overwritten>}

        control runner will skip overrides

        Override saved in db as:

        campus/site/device/point_name/override_source: Human, Schedule, Robot
        campus/site/device/point_name/override_initiated: True
        campus/site/device/point_name/overridden_status: True
        
"""

__docformat__ = 'reStructuredText'

import gevent
import logging
import sys
# from sqlalchemy import create_engine, text
import pandas as pd
import yaml
import json
from pytz import timezone
from datetime import datetime, timedelta, time
from volttron.platform.agent.utils import format_timestamp, get_aware_utc_now, parse_timestamp_string, process_timestamp
from volttron.platform.messaging import headers as headers_mod
from volttron.platform.agent import utils
from volttron.platform.vip.agent import Agent, Core, RPC, PubSub, Again, VIPError
from volttron.platform.scheduling import cron, periodic
import random
from time import sleep
import gevent

_log = logging.getLogger(__name__)
utils.setup_logging()
__version__ = "0.1"

REQUESTER_ID = 'requester_id'
TASK_ID = 'task_id'
TSTAT= 'devices/arc/tstat'
HPTES = 'hptes/modbus'
OVERRIDE_TOPICS = [
    'analysis/mpc/wcec/53113/Operation Mode',
]
DEVICE_TOPICS = []

def overridedetection(config_path, **kwargs):

    _log.debug("Config path: {}".format(config_path))
    try:
        config = utils.load_config(config_path)
    except Exception:
        config = {}
    if not config:
        _log.info("Using Agent defaults for starting configuration.")
    _log.debug("config_dict before init: {}".format(config))

    return OverrideDetection(**kwargs)


class OverrideDetection(Agent):
    """
    Agent used to test the functionality of the CSV driver
    """

    def __init__(self, **kwargs):
        # Configure the base agent
        super(OverrideDetection, self).__init__(**kwargs)
        _log.debug("vip_identity: " + self.core.identity)
        self.default_config = {}
        self.ts_last_thermostat = datetime.now()
        self.vip.config.subscribe(self.configure, actions=["NEW", "UPDATE"])

    def configure(self, config_name, action, contents):
        """
        Called after the Agent has connected to the message bus.
        If a configuration exists at startup this will be called before onstart

        Is called every time the configuration in the store changes.
        """

        self.config = self.default_config.copy()
        self.config.update(contents)
        self.ts_last_thermostat = datetime.now()
        self.turn_off = self.config.get('turn_off', False)
        # if self.turn_off.lower() == 'false':
        #     self.turn_off = False
        _log.debug("Configuring Agent")
        if self.turn_off:
            _log.debug("HPTES in off mode")
            self.off()

    @PubSub.subscribe('pubsub', TSTAT)
    def write_thermostat_values(self, peer, sender, bus,  topic, headers, message):
        sleep(5)
        if self.turn_off:
            _log.debug("TURNING OFF HPTES")
            self.off()
            return 
        else:
            self.set_baseline_setpoints(datetime.now())
        print(message)
        point_dict = message[0]
        active = int(point_dict['Stages Active'])
        state = int(point_dict['Cool/Heat State'])
        _log.debug(f"STAGES ACTIVE: {active}")
        _log.debug(f"COOL/HEAT STATE: {state}")
            # new_key = f'{topic}{k}'
            # if new_key in OVERRIDE_TOPICS:
            #     self.mpc_points.update({new_key:v})
            # if 'Stages Active' in k:
            #     active = v
            #     _log.debug(f"STAGES ACTIVE: {active}")
            # if 'Cool/Heat State' in k:
            #     state = v
            #     _log.debug(f"COOL/HEAT STATE: {state}")
                # state = 0 means cooling 
        if (state == 0) & (active == 1):
            _log.debug("sending call for cool")
            # Send call for cool 
            self.cool()
        elif (state == 1) & (active == 1):
            # Send call for heat
            _log.debug("sending call for heat")
            self.heat()
        elif (active == 0):
            # Send call for off
            self.off()
        else:
            _log.error("ACTIVE AND STATE NOT PRESENT")
            _log.error(f"ACTIVE: {active} | STATE: {state}")
            self.off()
        self.ts_last_thermostat = datetime.now()

    def set_baseline_setpoints(self, now):
        setpoints = {'arc/tstat/Manual Occupied Cool Setpoint': 70,
                    'arc/tstat/Manual Occupied Heat Setpoint': 67,
                    'arc/tstat/Manual Unoccupied Cool Setpoint': 85,
                    'arc/tstat/Manual Unoccupied Heat Setpoint': 60,
                    'arc/tstat/Occupancy Toggle': 0
                    }
        # if now.weekday() < 4:
        #     if 8 <= now.hour < 22: 
        #         setpoints.update({'1610101/bms_occ': 2,
        #                     '1610102/bms_occ': 2})
        # elif 4 <= now.weekday() < 6:
        #     if 8 <= now.hour < 20: 
        #         setpoints.update({'1610101/bms_occ': 2,
        #                     '1610102/bms_occ': 2})
        print('Current Hour: ', now.hour)
        if 8 <= now.hour < 20: 
            setpoints.update({'arc/tstat/Occupancy Toggle': 1})

        message = [(k, v) for k, v in setpoints.items()]
        self.actuate(message)
        return setpoints

    @Core.schedule(periodic(300))
    def safety_off(self):
        if (datetime.now() - self.ts_last_thermostat) > timedelta(seconds = 300):
            _log.error("NO NEW THERMOSTAT STATE. TURNING OFF")
            self.off()
    
    def cool(self):
        message = [('hptes/modbus/Supervisor_CallCold', True), ('hptes/modbus/Supervisor_Enabled', True), ('hptes/modbus/Supervisor_CallHot', False), ('hptes/modbus/xCommandOn', 1)]
        #message = [('hptes/modbus/Supervisor_CallCold', True)]
        self.actuate(message)

    def heat(self):
        message = [('hptes/modbus/Supervisor_CallHot', True),('hptes/modbus/Supervisor_CallCold', False), ('hptes/modbus/Supervisor_Enabled', True), ('hptes/modbus/xCommandOn', 1)]
        #message = [('hptes/modbus/Supervisor_CallHot', True)]
        self.actuate(message)

    def off(self):
        message = [('hptes/modbus/Supervisor_CallCold', False), ('hptes/modbus/Supervisor_CallHot', False), ('hptes/modbus/Supervisor_Enabled', True), ('hptes/modbus/xCommandOn', 0)]
        #message = [('hptes/modbus/Supervisor_CallCold', False), ('hptes/modbus/Supervisor_CallHot', False)]
        self.actuate(message)

    def actuate(self,point_setting):
        #will have to schedule all devices
        start = datetime.now()
        #end = datetime.now() + timedelta(minutes = self.frequency)
        # for testing 

        priority = 'LOW'
        task_id = TASK_ID
        task_id = str(random.randint(0,100000))
        devices = [f'hptes/modbus','arc/tstat']
        # Using start time so I don't get schedule conflicts.
        msg = [ [device, utils.format_timestamp(start), utils.format_timestamp(start)] for device in devices]
        try:
            result = self.vip.rpc.call('platform.actuator',
                                        'request_new_schedule',
                                           REQUESTER_ID,
                                           task_id,
                                           priority,
                                           msg).get(timeout=10)
        except Exception as e:
            print(e)
            _log.warning("Could not contact actuator. Is it running?")
        _log.info("schedule result {}".format(result))

        print('Point Setting', point_setting)
        result = self.vip.rpc.call('platform.actuator',
                                    'set_multiple_points',
                                    REQUESTER_ID,
                                    point_setting).get(timeout=20)
        if result:
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
        
    # Not sure if this will work now
    # @Core.schedule(periodic(60))
    # def test_function(self):
    #     for i, entry in enumerate(OVERRIDE_TOPICS):
    #         parts = entry.rsplit('/', 1)
    #         topic = parts[0]
    #         message = {parts[1]: i}
    #         self.publish(topic, message)
    #     sleep(30)
    #     self.periodic_publish()


def main():
    """Main method called to start the agent."""
    utils.vip_main(overridedetection,
                   version=__version__)


if __name__ == '__main__':
    # Entry point for script
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
