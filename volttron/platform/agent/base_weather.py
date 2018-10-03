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
import pint
import json
import sqlite3
import datetime
import threading
from functools import wraps
from abc import abstractmethod
from Queue import Queue, Empty
import gevent
from gevent import get_hub
from volttron.platform.agent import utils
#from utils import setup_logging
from volttron.platform.vip.agent import *
from volttron.platform.async import AsyncCall
from volttron.platform.messaging import headers
from volttron.platform.messaging.health import (STATUS_BAD,
                                                STATUS_UNKNOWN,
                                                STATUS_GOOD,
                                                STATUS_STARTING,
                                                Status)

__version__ = "0.1.0"

# TODO setup logging doesn't work
#utils.setup_logging(logging.DEBUG)
#setup_logging()
_log = logging.getLogger(__name__)

HEADER_NAME_DATE = headers.DATE
HEADER_NAME_CONTENT_TYPE = headers.CONTENT_TYPE

STATUS_KEY_PUBLISHING = "publishing"
STATUS_KEY_CACHE_FULL = "cache_full"


class BaseWeatherAgent(Agent):
    """Creates weather services based on the json objects from the config,
    uses the services to collect and publish weather data"""

    def __init__(self,
                 service_name=None,
                 api_key=None,
                 max_size_gb=None,
                 polling_locations=None,
                 poll_interval=None,
                 **kwargs):

        super(BaseWeatherAgent, self).__init__(**kwargs)
        self._service_name = service_name
        self._async_call = AsyncCall()
        self._api_key = api_key
        self._max_size_gb = max_size_gb
        self.polling_locations = polling_locations
        self.poll_interval = poll_interval
        self._default_config = {
                                "service": self._service_name,
                                "api_key": self._api_key,
                                "max_size_gb": self._max_size_gb,
                                "polling_locations": self.polling_locations,
                                "poll_interval": self.poll_interval
                               }
        self.unit_registry = pint.UnitRegistry()
        self.weather_mapping = {}
        self._api_services = {"get_current_weather": {"type": "current",
                                                      "update_interval": None,
                                                      "accepted_location_formats": [],
                                                      "description": "Params: locations ([{type: value},...])"},
                              "get_hourly_forecast": {"type": "forecast",
                                                      "update_interval": None,
                                                      "accepted_location_formats": [],
                                                      "description": "Params: locations ([{type: value},...])" },
                              "get_hourly_historical": {"type": "history",
                                                        "update_interval": None,
                                                        "accepted_location_formats": [],
                                                        "description": "Params: locations ([{type: value},...]), "
                                                                       "start_date (date), end_date(date)"}
                              }
        # TODO finish status context
        self._current_status_context = {
            STATUS_KEY_CACHE_FULL: False
        }

        # TODO manage health with respect to these conditions
        self.successfully_publishing = None
        if len(self.polling_locations):
            self._current_status_context[STATUS_KEY_PUBLISHING] = True
            self.successfully_publishing = True

        self._cache = WeatherCache(service_name=self._service_name, api_services=self._api_services, max_size_gb=self._max_size_gb)

        self.vip.config.set_default("config", self._default_config)
        self.vip.config.subscribe(self._configure, actions=["NEW", "UPDATE"], pattern="config")

    # Configuration methods

    # TODO update documentation
    def register_service(self, service_function_name, interval, accepted_formats=None, description=None):
        """Called in a weather agent's __init__ function to add api services to the api services dictionary.
        :param service_function_name: function call name for an api feature
        :param interval: datetime timedelta object describing the length of time between api updates
        :param accepted_formats: list of format names as strings for location validation
        :param description: optional description string describing the method's usage
        """
        if not isinstance(interval, datetime.timedelta):
            raise ValueError("interval must be a valid datetime timedelta object.")
        self._api_services[service_function_name] = {"update_interval": interval,
                                                     "accepted_location_formats": accepted_formats,
                                                     "description": description}

    # TODO docs
    def remove_service(self, service_function_name):
        """

        :param service_function_name: a function call name for an api feature to be removed
        """
        self._api_services.pop(service_function_name)

    # TODO update documentation
    def set_update_interval(self, service_name, interval):
        """

        :param service_name: a function call name for an api feature to be updated
        :param interval: datetime timedelta object specifying the length of time between api updates
        """
        if not isinstance(interval, datetime.timedelta):
            raise ValueError("interval must be a valid datetime timedelta object.")
        if service_name in self._api_services:
            self._api_services[service_name]["update_interval"] = interval
        else:
            raise ValueError("{} not found in api features.".format(service_name))

    def set_accepted_location_formats(self, service_name, accepted_formats):
        """

        :param service_name:
        :param accepted_formats: list of strings containing format names to be included when validating a location for
        an api feature call
        """
        if not len(accepted_formats):
            raise ValueError("At least one accepted location format must be provided")
        for string in accepted_formats:
            if not isinstance(string, str):
                raise ValueError("Accepted formats are to be strings only.")
        if service_name in self._api_services:
            self._api_services[service_name]["accepted_location_formats"] = accepted_formats
        else:
            raise ValueError("{} not found in api features.".format(service_name))

    def update_default_config(self, config):
        """
        May be called by historians to add to the default configuration for its
        own use.
        :param config: configuration dictionary
        """
        self._default_config.update(config)
        self.vip.config.set_default("config", self._default_config)

    def parse_weather_mapping(self, config_dict):
        """
        Parses the registry config, which should contain a mapping of service points to standardized points, with
        specified unit
        :param config_dict: registry configuration dictionary containing mappings from points included in api, to points
        included in the NOAA standard weather structure. Points listed without a standard name will be included without
        renaming or unit conversion
        """
        for map_item in config_dict:
            service_point_name = map_item.get("Service_Point_Name")
            if service_point_name:
                standard_point_name = map_item.get("Standard_Point_Name")
                standardized_units = map_item.get("Standardized_Units")
                service_units = map_item.get("Service_Units")
                self.weather_mapping[service_point_name] = {"Standard_Point_Name": standard_point_name,
                                                            "Standardized_Units": standardized_units,
                                                            "Service_units": service_units}

    # TODO copy documentation?
    def _configure(self, config_dict, registry_config):
        """

        :param config_dict:
        :param registry_config:
        """
        self.vip.heartbeat.start()
        _log.info("Configuring weather agent.")
        config = self._default_config.copy()
        config.update(config_dict)
        try:
            api_key = config.get("api_key")
            max_size_gb = config.get("max_size_gb")
            polling_locations = config.get("poll_locations")
            poll_interval = config.get("poll_interval")
            if max_size_gb is not None:
                max_size_gb = float(max_size_gb)
            self.parse_weather_mapping(registry_config)

        except ValueError:
            _log.error("Failed to load base weather agent settings. Settings not applied!")
            return
        self._api_key = api_key
        self._max_size_gb = max_size_gb
        self.polling_locations = polling_locations
        self.poll_interval = poll_interval
        try:
            self.configure(config)
        except:
            _log.error("Failed to load weather agent settings.")

    def configure(self, configuration):
        """Optional, may be implemented by a concrete implementation to add support for the configuration store.
        Values should be stored in this function only.

        The process thread is stopped before this is called if it is running. It is started afterwards.
        :param configuration:
        """
        pass

    # RPC, helper and abstract methods to be used by concrete implementations of the weather agent

    # TODO update spec to match name
    # Add doc string
    @RPC.export
    def get_api_features(self):
        """

        :return: {function call: description string}
        """
        features = {}
        for service_name in self._api_services:
            features[service_name] = self._api_services[service_name]["description"]
        return features

    # TODO docs
    @abstractmethod
    def validate_location(self, accepted_formats, location):
        """"""

    # TODO add doc
    @RPC.export
    def get_current_weather(self, locations):
        data = []
        service_name = "get_current_weather"
        interval = self._api_services[service_name]["update_interval"]
        if not isinstance(interval, datetime.timedelta):
            raise RuntimeError("interval for {} is invalid: {}.".format(service_name, interval))
        for location in locations:
            record = []
            if not self.validate_location(self._api_services[service_name]["accepted_location_formats"], location):
                raise ValueError("Invalid location: {}".format(location))
            most_recent_for_location = self.get_cached_current_data(service_name, location)
            if most_recent_for_location:
                current_time = datetime.datetime.utcnow()
                update_window = current_time - interval
                if most_recent_for_location[2] > update_window:
                    record = [
                            most_recent_for_location[1],
                            most_recent_for_location[2],
                            json.loads(most_recent_for_location[3])
                            ]
                    data.append(record)
            if not len(record):
                try:
                    response = self.query_current_weather(location)
                except:
                    # TODO might need to do a different thing here
                    response = []
                if len(response):
                    storage_record = [response[0], utils.parse_timestamp_string(response[1]), json.dumps(response[2])]
                    self.store_weather_records(service_name, storage_record)
                data.append(response)

        return data

    @abstractmethod
    def query_current_weather(self, location):
        """

        :param location:
        :return: dictionary containing a single record of data
        """

    # TODO add docs
    @RPC.export
    def get_hourly_forecast(self, locations, hours=None):
        data = []
        service_name = "get_hourly_forecast"
        interval = self._api_services[service_name]["update_interval"]
        if not isinstance(interval, datetime.timedelta):
            raise RuntimeError("interval for {} is invalid: {}.".format(service_name, interval))
        for location in locations:
            if not self.validate_location(self._api_services[service_name]["accepted_location_formats"], location):
                raise ValueError("Invalid location: {}".format(location))
            most_recent_for_location = self.get_cached_forecast_data(service_name, location)
            location_data = []
            if most_recent_for_location:
                current_time = datetime.datetime.utcnow()
                update_window = current_time - interval
                generation_time = most_recent_for_location[0][2]
                if generation_time >= update_window:
                    for record in most_recent_for_location:
                        entry = [record[1], record[2], record[3], json.loads(record[4])]
                        location_data.append(entry)
            if not len(location_data) or (hours and len(data) < hours):
                try:
                    response = self.query_hourly_forecast(location)
                except RuntimeError:
                    # TODO might need to do a different thing here
                    response = []
                storage_records = []
                for item in response:
                    storage_record = [item[0], utils.parse_timestamp_string(item[1]),
                                      utils.parse_timestamp_string(item[2]), json.dumps(item[3])]
                    storage_records.append(storage_record)
                    location_data.append(item)
                if len(storage_records):
                    self.store_weather_records(service_name, storage_records)
            for record in location_data:
                data.append(record)
        return data

    # TODO docs
    @abstractmethod
    def query_hourly_forecast(self, location):
        """

        :param location:
        :return: list containing 1 dictionary per data record in the forecast set
        """

    # TODO do by date, add docs
    @RPC.export
    def get_hourly_historical(self, locations, start_date, end_date):
        data = []
        service_name = "get_hourly_historical"
        start_datetime = datetime.datetime.combine(start_date, datetime.time())
        end_datetime = datetime.datetime.combine(end_date, datetime.time()) + \
                       (datetime.timedelta(days=1) - datetime.timedelta(milliseconds=1))
        # TODO
        for location in locations:
            if not self.validate_location(self._api_services[service_name]["accepted_location_formats"], location):
                raise ValueError("Invalid Location:{}".format(location))
            current = start_datetime
            while current <= end_datetime:
                records = []
                cached_history = self.get_cached_historical_data(service_name, location, current)
                if cached_history:
                    for item in cached_history:
                        record = [item[1], item[2], json.loads(item[3])]
                        records.append(record)
                if not len(records):
                    response = self.query_hourly_historical(location, current)
                    storage_records = []
                    for item in response:
                        records.append(item)
                        record = [item[0], item[1], json.dumps(item[2])]
                        storage_records.append(record)
                    self.store_weather_records(service_name, storage_records)
                for record in records:
                    data.append(record)
                current = current + datetime.timedelta(days=1)
        return data

    @abstractmethod
    def query_hourly_historical(self, location, start_date, end_date):
        """

        :param location:
        :param date:
        :return: list containing 1 dictionary per data record in the history set
        """

    # TODO docs
    def poll_for_locations(self):
        topic = "weather/{}/current/{}/all"
        data = self.query_current_weather(self.polling_locations)
        for record in data:
            poll_topic = topic.format("poll", record["location"])
            self.publish_response(poll_topic, record)

    # TODO docs
    def publish_response(self, topic, publish_item):
        publish_headers = {HEADER_NAME_DATE: utils.format_timestamp(utils.get_aware_utc_now()),
                           HEADER_NAME_CONTENT_TYPE: headers.CONTENT_TYPE}
        self.vip.pubsub.publish(peer="pubsub", topic=topic, message=publish_item, headers=publish_headers)

    def manage_unit_conversion(self, from_units, value, to_units):
        """
        Used to convert units from a query response to the expected standardized units
        :param from_units: pint formatted unit string for the current value
        :param value: magnitude of a measurement
        :param to_units: pint formatted unit string for the output value
        :return: magnitude of measurement in the desired units
        """
        if ((1 * self.unit_registry.parse_expression(from_units)) ==
                (1 * self.unit_registry.parse_expression(to_units))):
            return value
        else:
            updated_value = (value * self.unit_registry(from_units)).to(self.unit_registry(to_units)).magnitude
            return updated_value

    # TODO docs
    # methods to hide cache functionality from concrete weather agent implementations

    # TODO docs
    @abstractmethod
    def get_location_string(self, location):
        """"""

    def get_cached_current_data(self, request_name, location):
        location_string = self.get_location_string(location)
        return self._cache.get_current_data(request_name, location_string)

    def get_cached_forecast_data(self, request_name, location):
        location_string = self.get_location_string(location)
        return self._cache.get_forecast_data(request_name, location_string)

    def get_cached_historical_data(self, request_name, location, date_timestamp):
        location_string = self.get_location_string(location)
        return self._cache.get_historical_data(request_name, location_string, date_timestamp)

    def store_weather_records(self, service_name, records):
        """

        :param service_name:
        :param records:
        """
        cache_full = self._cache.store_weather_records(service_name, records)
        # TODO status alerts
        self._current_status_context[STATUS_KEY_CACHE_FULL] = cache_full
        return cache_full

    # TODO
    # Status management methods

    def _get_status_from_context(self, context):
        status = STATUS_GOOD
        if context.get("cache_full") or (not context.get("publishing") and len(self.polling_locations)):
            status = STATUS_BAD
        return status

    def _update_status_callback(self, status, context):
        self.vip.health.set_status(status, context)

    def _update_status(self, updates):
        context_copy, new_status = self._update_and_get_context_status(updates)
        self._async_call.send(None, self._update_status_callback, new_status, context_copy)

    def _send_alert_callback(self, status, context, key):
        self.vip.health.set_status(status, context)
        alert_status = Status()
        alert_status.update_status(status, context)
        self.vip.health.send_alert(key, alert_status)

    def _update_and_get_context_status(self, updates):
        self._current_status_context.update(updates)
        context_copy = self._current_status_context.copy()
        new_status = self._get_status_from_context(context_copy)
        return context_copy, new_status

    def _send_alert(self, updates, key):
        context_copy, new_status = self._update_and_get_context_status(updates)
        self._async_call.send(None, self._send_alert_callback, new_status, context_copy, key)

    # TODO docs
    # Agent lifecycle methods

    @Core.receiver("onstart")
    def setup(self, sender, **kwargs):
        if self.polling_locations:
            self.core.periodic(self.poll_interval, self.poll_for_locations)

    @Core.receiver("onstop")
    def stopping(self, sender, **kwargs):
        self._cache.close()

# TODO docs
class WeatherCache:
    """Caches data to help reduce the number of requests to the API"""
    def __init__(self,
                 service_name="default",
                 api_services=None,
                 max_size_gb=1,
                 check_same_thread=True):
        """

        :param service_name: Name of the weather service (i.e. weather.gov)
        :param api_services: dictionary from BaseAgent, used to determine table names
        :param max_size_gb: maximum size in gigabytes of the sqlite database file, useful for deployments with limited
        storage capacity
        :param check_same_thread:
        """
        self._service_name = service_name
        # TODO need to alter the file path for the database
        self._db_file_path = self._service_name + ".sqlite"
        self._api_services = api_services
        self._max_size_gb = max_size_gb
        self._sqlite_conn = None
        self._setup_cache(check_same_thread)

    # cache setup methods

    # TODO calculating max_storage_bytes has memory error?
    def _setup_cache(self, check_same_thread):
        """
        prepare the cache to begin processing weather data
        :param check_same_thread:
        """
        _log.debug("Setting up backup DB.")
        self._sqlite_conn = sqlite3.connect(
            self._db_file_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=check_same_thread)
        _log.info("connected to database {} sqlite version: {}".format(self._service_name, sqlite3.version))
        self.create_tables()
        cursor = self._sqlite_conn.cursor()
        if self._max_size_gb is not None:
            cursor.execute('''PRAGMA page_size''')
            page_size = cursor.fetchone()[0]
            max_storage_bytes = self._max_size_gb * 1024 ** 3
            _log.error(self._max_size_gb)
            self.max_pages = max_storage_bytes / page_size
            self.manage_cache_size()
        cursor.close()

    def create_tables(self):
        """
        Checks to see if the proper tables and table columns are in the database, creates them if they are not.
        """
        cursor = self._sqlite_conn.cursor()
        for service_name in self._api_services:
            table_type = self._api_services[service_name]["type"]
            if table_type == "forecast":
                create_table = """CREATE TABLE IF NOT EXISTS {}
                                (ID INTEGER PRIMARY KEY ASC,
                                 LOCATION TEXT NOT NULL,
                                 GENERATION_TIME TIMESTAMP NOT NULL,
                                 FORECAST_TIME TIMESTAMP NOT NULL,
                                 POINTS TEXT NOT NULL);""".format(service_name)
            elif table_type == "current" or table_type == "history":
                create_table ="""CREATE TABLE IF NOT EXISTS {}
                                (ID INTEGER PRIMARY KEY ASC,
                                 LOCATION TEXT NOT NULL,
                                 OBSERVATION_TIME TIMESTAMP NOT NULL, 
                                 POINTS TEXT NOT NULL);""".format(service_name)
            else:
                raise ValueError("Invalid table type {} for table {}.".format(table_type, service_name))
            _log.debug(create_table)
            try:
                cursor.execute(create_table)
                self._sqlite_conn.commit()
            except sqlite3.Error as err:
                _log.error("Unable to create database table: {}".format(err))
            if table_type == "forecast":
                expected_columns = ["ID", "LOCATION", "GENERATION_TIME", "FORECAST_TIME", "POINTS"]
            else:
                expected_columns = ["ID", "LOCATION", "OBSERVATION_TIME", "POINTS"]
            column_names = []
            table_info = cursor.execute("PRAGMA table_info({})".format(service_name)).fetchall()
            for row in table_info:
                column_names.append(row[1])
            for column_name in expected_columns:
                if column_name not in column_names:
                    delete_query = "DROP TABLE IF EXISTS {};".format(service_name)
                    cursor.execute(delete_query)
                    self._sqlite_conn.commit()
                    _log.debug(delete_query)
                    if table_type == "forecast":
                        create_table = """CREATE TABLE {}
                                        (ID INTEGER PRIMARY KEY ASC,
                                         LOCATION TEXT NOT NULL,
                                         GENERATION_TIME TIMESTAMP NOT NULL,
                                         FORECAST_TIME TIMESTAMP NOT NULL,
                                         POINTS TEXT NOT NULL);""".format(service_name)
                    elif table_type == "current" or table_type == "history":
                        create_table = """CREATE TABLE {}
                                        (ID INTEGER PRIMARY KEY ASC,
                                         LOCATION TEXT NOT NULL,
                                         OBSERVATION_TIME TIMESTAMP NOT NULL, 
                                         POINTS TEXT NOT NULL);""".format(service_name)
                    _log.debug(create_table)
                    cursor.execute(create_table)
                    self._sqlite_conn.commit()
                break
        cursor.close()

    # TODO return the json strings as dictionaries
    # cache data storage and retrieval methods
    # TODO look these over, remove request time
    def get_current_data(self, service_name, location):
        """
        Retrieves the most recent current data by location
        :param service_name:
        :param location:
        :return: a single current weather observation record
        """
        try:
            cursor = self._sqlite_conn.cursor()
            query = """SELECT ID, LOCATION, OBSERVATION_TIME, POINTS FROM {} WHERE OBSERVATION_TIME = (SELECT MAX(OBSERVATION_TIME) FROM {} 
            WHERE LOCATION = '{}') AND LOCATION = '{}' LIMIT 1;"""\
                .format(service_name, service_name, location, location)
            _log.debug(query)
            cursor.execute(query)
            data = cursor.fetchone()
            cursor.close()
            return data
        except sqlite3.Error as e:
            _log.error("Error fetching current data from cache: {}".format(e))
            return None

    def get_forecast_data(self, service_name, location):
        """
        Retrieves the most recent forecast record set (forecast should be a time-series) by location
        :param service_name:
        :param location:
        :return: list of forecast records
        """
        try:
            cursor = self._sqlite_conn.cursor()
            query = """SELECT ID, LOCATION, GENERATION_TIME, FORECAST_TIME, POINTS FROM {} WHERE LOCATION = '{}' AND GENERATION_TIME =
                    (SELECT MAX(GENERATION_TIME) FROM {} WHERE LOCATION = '{}') ORDER BY FORECAST_TIME ASC;"""\
                .format(service_name, location, service_name, location)
            _log.debug(query)
            cursor.execute(query)
            data = cursor.fetchall()
            cursor.close()
            return data
        except sqlite3.Error as e:
            _log.error("Error fetching forecast data from cache: {}".format(e))

    def get_historical_data(self, service_name, location, date_timestamp):
        """
        Retrieves historical data over the the given time period by location
        :param service_name:
        :param location:
        :param date_timestamp:
        :return: list of historical records
        """
        start_timestamp = date_timestamp
        end_timestamp = date_timestamp + (datetime.timedelta(days=1)-datetime.timedelta(milliseconds=1))
        if service_name not in self._api_services:
            raise ValueError("service {} does not exist in the agent's services.".format(service_name))
        try:
            cursor = self._sqlite_conn.cursor()
            query = """SELECT ID, LOCATION, OBSERVATION_TIME, POINTS FROM {} WHERE LOCATION = ? AND 
            OBSERVATION_TIME BETWEEN ? AND ? ORDER BY OBSERVATION_TIME ASC;""".format(service_name)
            _log.debug(query)
            cursor.execute(query, (location, start_timestamp, end_timestamp))
            data = cursor.fetchall()
            cursor.close()
            return data
        except sqlite3.Error as e:
            _log.error("Error fetching historical data from cache: {}".format(e))

    def store_weather_records(self, service_name, records):
        """
        Request agnostic method to store weather records in the cache.
        :param service_name:
        :param records: expects a list of records (as lists) formatted to match tables
        :return: boolean value representing whether or not the cache is full
        """
        if service_name not in self._api_services:
            raise ValueError("service {} does not exist in the agent's services.".format(service_name))
        if self._max_size_gb is not None:
            self.manage_cache_size()
        cursor = self._sqlite_conn.cursor()
        request_type = self._api_services[service_name]["type"]
        if request_type == "forecast":
            query = "INSERT INTO {} (LOCATION, GENERATION_TIME, FORECAST_TIME, POINTS)" \
                    " VALUES (?, ?, ?, ?)".format(service_name)
        else:
            query = "INSERT INTO {} (LOCATION, OBSERVATION_TIME, POINTS) VALUES (?, ?, ?)"\
                .format(service_name)
        _log.debug(query)
        try:
            if request_type == "current":
                cursor.execute(query, records)
                self._sqlite_conn.commit()
            else:
                cursor.executemany(query, records)
                self._sqlite_conn.commit()
        except sqlite3.Error as e:
            _log.info(query)
            _log.error("Failed to store data in the cache: {}".format(e))
        cache_full = False
        if self._max_size_gb is not None:
            cache_full = self.page_count(cursor) >= self.max_pages
        cursor.close()
        return cache_full

    # cache management/ lifecycle methods

    def page_count(self, cursor):
        cursor.execute("PRAGMA page_count")
        return cursor.fetchone()[0]

    # TODO This needs extensive testing
    def manage_cache_size(self):
        """
        Removes data from the weather cache until the cache is a safe size. prioritizes removal from current, then
        forecast, then historical request types
        """
        if self._max_size_gb:
            cursor = self._sqlite_conn.cursor()
            if self._max_size_gb is not None:
                row_count_query = "SELECT COUNT(*) FROM {}"
                row_counts = {}
                for table in self._api_services:
                    row_counts[table] = int(cursor.execute(row_count_query.format(table)).fetchone()[0])
                priority = 1
                while self.page_count(cursor) > self.max_pages:
                    if priority == 1:
                        for table in row_counts:
                        # Remove all but the most recent 'current' records
                            if self._api_services[table]["type"] == "current" and row_counts[table] > 1:
                                # TODO get a list of max observation times per location
                                # TODO delete all records for that location with date < that max time
                                row_counts[table] = int(cursor.execute(row_count_query.format(table)).fetchone()[0])
                    elif priority == 2:
                        for table in row_counts:
                        # Remove all but the most recent 'forecast' records
                            if self._api_services[table]["type"] == "forecast" and row_counts[table] > 1:
                                # TODO get a list of max generation times per location
                                # TODO delete all records for that location with date < that max time
                                row_counts[table] = int(cursor.execute(row_count_query.format(table)).fetchone()[0])

                    elif priority == 3:
                        for table in row_counts:
                        # Remove historical records in batches of 100 until the table is of appropriate size
                            if self._api_services[table]["type"] == "history" and row_counts[table] > 1:
                                # TODO remove the oldest 100 observation times for each location
                                row_counts[table] = int(cursor.execute(row_count_query.format(table)).fetchone()[0])
                    if priority < 3:
                        priority += 1

    def close(self):
        """Close the sqlite database connection when the agent stops"""
        self._sqlite_conn.close()
        self._sqlite_conn = None


# Code reimplemented from https://github.com/gilesbrown/gsqlite3
def _using_threadpool(method):
    @wraps(method, ['__name__', '__doc__'])
    def apply(*args, **kwargs):
        return get_hub().threadpool.apply(method, args, kwargs)
    return apply


class AsyncWeatherCache(WeatherCache):
    """Asynchronous weather cache wrapper for use with gevent"""
    def __init__(self, **kwargs):
        kwargs["check_same_thread"] = False
        super(AsyncWeatherCache, self).__init__(**kwargs)


# TODO documentation
for method in [WeatherCache.get_current_data,
               WeatherCache.get_forecast_data,
               WeatherCache.get_historical_data,
               WeatherCache._setup_cache,
               WeatherCache.store_weather_records]:
    setattr(AsyncWeatherCache, method.__name__, _using_threadpool(method))

