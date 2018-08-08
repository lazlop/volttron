# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:
#
# Copyright (c) 2017, Battelle Memorial Institute
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and documentation are
# those of the authors and should not be interpreted as representing official,
# policies either expressed or implied, of the FreeBSD Project.
#

# This material was prepared as an account of work sponsored by an
# agency of the United States Government.  Neither the United States
# Government nor the United States Department of Energy, nor Battelle,
# nor any of their employees, nor any jurisdiction or organization
# that has cooperated in the development of these materials, makes
# any warranty, express or implied, or assumes any legal liability
# or responsibility for the accuracy, completeness, or usefulness or
# any information, apparatus, product, software, or process disclosed,
# or represents that its use would not infringe privately owned rights.
#
# Reference herein to any specific commercial product, process, or
# service by trade name, trademark, manufacturer, or otherwise does
# not necessarily constitute or imply its endorsement, recommendation,
# r favoring by the United States Government or any agency thereof,
# or Battelle Memorial Institute. The views and opinions of authors
# expressed herein do not necessarily state or reflect those of the
# United States Government or any agency thereof.
#
# PACIFIC NORTHWEST NATIONAL LABORATORY
# operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
# under Contract DE-AC05-76RL01830

# }}}
import logging
import os
import sys
from abc import abstractmethod
import requests
from volttron.platform.agent import utils
from volttron.platform.vip.agent import *

_log = logging.getLogger(__name__)

# TODO testing
testing_config_path = os.path.dirname(os.path.realpath(__file__))
testing_config_path += "/config.config"

class WeatherSchema:
    def __init__(self,
                 schema_mapping):
        self.base_schema = schema_mapping
        self.alternate_schema = {}

    def map_schema(self, mapping):
        for key, value in mapping:
            if key in self.base_schema:
                self.base_schema[key] = value
            else:
                self.alternate_schema[key] = value


class WeatherCache:
    # TODO ask about sqlite for caching

# class WeatherAgent():
class BaseWeatherAgent(Agent):
    """Creates weather services based on the json objects from the config,
    uses the services to collect and publish weather data"""

    # TODO set up defaults for a weather agent here
    def __init__(self,
                 api_key="",
                 base_url=None,
                 locations=[],
                 **kwargs):
        # TODO figure out if this is necessary
        super(BaseWeatherAgent, self).__init__(**kwargs)
        # TODO set protected variables
        self._api_key = api_key
        self._base_url = base_url
        self._locations = locations
        # TODO build from init parameters

        self._default_config = {"api_key": self._api_key}
        self.vip.config.set_default("config", self._default_config)

    @Core.receiver('onstart')
    def setup(self, config):
        try:
            self.configure(config)
            # TODO check this, might need to go in configure
            self.schema = WeatherSchema()
        except Exception as e:
            _log.error("Failed to load weather agent settings.")
        # TODO ?
        # self.vip.config.subscribe(self._configure, actions=["NEW", "UPDATE"], pattern="config")


    # TODO schema mapping should be contained in the config.config, no registry config necessary?
    @abstractmethod
    def configure(self, config):
        """Unimplemented method stub."""
        pass

    # TODO
    @abstractmethod
    def get_current_weather(self, location):
        pass

    # TODO
    @abstractmethod
    def get_forecast(self, location):
        pass

    @abstractmethod
    def get_historical_weather(self, location, start_period, end_period):
        pass




