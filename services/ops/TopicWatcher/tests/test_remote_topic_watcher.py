
# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:
#
# Copyright 2017, Battelle Memorial Institute.
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

import json
import sqlite3

import gevent
import os
import pytest

from volttron.platform import get_ops, get_examples
from volttron.platform.agent.known_identities import PLATFORM_TOPIC_WATCHER
from volttron.platform.agent.utils import get_aware_utc_now

alert_messages = {}

@pytest.mark.alert
def test_remote_alert_publish(get_volttron_instances):
    """
    Test alert to remote agent
    :param agent:
    :param cleanup_db:
    :return:
    """

    volttron_instance1, volttron_instance2 = get_volttron_instances(2)

    volttron_instance1.allow_all_connections()
    volttron_instance2.allow_all_connections()

    gevent.sleep(3)
    agent = volttron_instance1.build_agent()

    def onmessage(peer, sender, bus, topic, headers, message):
        global alert_messages

        alert = json.loads(message)["context"]

        try:
            alert_messages[alert] += 1
        except KeyError:
            alert_messages[alert] = 1
        print("In on message: {}".format(alert_messages))

    agent.vip.pubsub.subscribe(peer='pubsub',
                               prefix='alerts',
                               callback=onmessage)

    config = {
        "group1": {
            "fakedevice": 5,
            "fakedevice2/all": {
                "seconds": 5,
                "points": ["point"]
            }
        },
        "publish-settings": {
            "publish-local": False,
            "publish-remote": True,
            "remote": {
                "identity": "remote-agent",
                "serverkey": volttron_instance1.serverkey,
                "vip-address": volttron_instance1.vip_address
            }
        }
    }

    alert_uuid = volttron_instance2.install_agent(
        agent_dir=get_ops("TopicWatcher"),
        config_file=config,
        vip_identity=PLATFORM_TOPIC_WATCHER
    )

    gevent.sleep(6)

    assert alert_messages

