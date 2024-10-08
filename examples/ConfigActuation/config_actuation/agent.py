# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2023 Battelle Memorial Institute
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# ===----------------------------------------------------------------------===
# }}}

import datetime
import logging
import os
import sys
import pytz
from volttron.platform.vip.agent import Agent
from volttron.platform.agent import utils

utils.setup_logging()
_log = logging.getLogger(__name__)
__version__ = '0.1'

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

        start = utils.get_aware_utc_now()
        end = utils.get_aware_utc_now() + datetime.timedelta(minutes=1)
        msg = [[device, str(start), str(end)]]
        print(start,str(end))
        try:
            result = self.vip.rpc.call('platform.actuator',
                                       'request_new_schedule',
                                       REQUESTER_ID,
                                       TASK_ID,
                                       'LOW',
                                       msg).get(timeout=10)
        except Exception as e:
            _log.warning("Could not contact actuator. Is it running?")
            print(e)
            return

        _log.info("schedule result {}".format(result))
        if result['result'] != 'SUCCESS':
            return

        topics_values = []
        for point, value in contents.items():
            full_topic = os.path.join(device, point)
            topics_values.append((full_topic, value))
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
            self.core.schedule(utils.parse_timestamp_string("2021-12-03T18:52:18").astimezone(pytz.timezone('US/Pacific')),self.set,(default))
        except Exception as e:
            print(e)

        self.vip.rpc.call('platform.actuator',
                          'request_cancel_schedule',
                          REQUESTER_ID,
                          TASK_ID).get()
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
