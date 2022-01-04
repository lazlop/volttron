# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:

import datetime
import logging
import os
import sys

from datetime import datetime
import pytz
# from dateutil.parser import parse
# from dateutil.tz import tzutc, tzoffset

from volttron.platform.vip.agent import Agent
from volttron.platform.agent import utils

utils.setup_logging()
_log = logging.getLogger(__name__)
__version__ = '1'

REQUESTER_ID = 'requester_id'
TASK_ID = 'task_id'

class ConfigActuation(Agent):
    """
    This agent is used to demonstrate scheduling and acutation of devices
    when a configstore item is added or updated.
    """

    def __init__(self, config_path, **kwargs):
        super(ConfigActuation, self).__init__(**kwargs)

        self.vip.config.subscribe(self.configure, actions=["NEW", "UPDATE"])

    def configure(self, config_name, action, contents):
        device = config_name

        topics_values = []
        for point, value in contents.items():
            full_topic = os.path.join(device, point)
            topics_values.append((full_topic, value.get("value")))
            print(topics_values)
            start = value.get("start", format_timestamp(datetime.now()))
            end = value.get("end", format_timestamp(datetime.now()+ datetime.timedelta(minutes=1)))
            priority = value.get("priority", 'LOW')
            task_id = value.get('task_id','task_id')

        msg = [[device, start, end]]

        try:
            result = self.vip.rpc.call('platform.actuator',
                                       'request_new_schedule',
                                       REQUESTER_ID,
                                       task_id,
                                       priority,
                                       msg).get(timeout=10)
        except Exception as e:
            _log.warning("Could not contact actuator. Is it running?")
            print(e)
            return

        _log.info("schedule result {}".format(result))
        if result['result'] != 'SUCCESS':
            return

        print(full_topic)
        try:
            result = self.vip.rpc.call('platform.actuator',
                                       'get_point',
                                       full_topic).get(timeout=10)
            print("HEY LOOK AT THIS",result)
            default = []
            default.append((full_topic,result))
        except Exception as e:
            print(e)
        try:
            result = self.vip.rpc.call('platform.actuator',
                                       'set_multiple_points',
                                       REQUESTER_ID,
                                       topics_values).get(timeout=10)
            self.core.schedule(utils.parse_timestamp_string(end).astimezone(pytz.timezone('US/Pacific')),self.set,(default))
        except Exception as e:
            print(e)

        self.vip.rpc.call('platform.actuator',
                          'request_cancel_schedule',
                          REQUESTER_ID,
                          task_id).get()
        
    def set(self,default):
            print("NOW!")
            result = self.vip.rpc.call('platform.actuator',
                                       'set_multiple_points',
                                       REQUESTER_ID,
                                       default).get(timeout=10)


def main(argv=sys.argv):
    utils.vip_main(ConfigActuation)


if __name__ == '__main__':
    # Entry point for script
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
