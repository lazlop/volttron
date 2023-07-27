'''
Insert Copywrite
'''
import logging
import requests
import json

import xmltodict
from typing import Union

from volttron.platform.agent import utils
from volttron.platform.agent.known_identities import CONFIGURATION_STORE, PLATFORM_DRIVER
from platform_driver.interfaces import BaseInterface, BaseRegister, BasicRevert

_log = logging.getLogger(__name__)
__version__ = "0.5"
type_mapping = {"string": str,
                "int": int,
                "integer": int,
                "float": float,
                "bool": bool,
                "boolean": bool}


class Interface(BasicRevert, BaseInterface):
    """
    Interface implementation for wrapping around the Ecobee thermostat API
    """

    def __init__(self, **kwargs):
        super(Interface, self).__init__(**kwargs)
        # Configuration value defaults

    def configure(self, config_dict, registry_config_str):
        """
        Interface configuration callback
        :param config_dict: Driver configuration dictionary
        :param registry_config_str: Driver registry configuration dictionary
        """
        self.config_dict = config_dict
        self.registry_config_str = registry_config_str

        self.url = self.config_dict.get("url")
        self.username = self.config_dict.get('username')
        self.password = self.config_dict.get('password')
        self.object = self.config_dict.get('object')
        self.thermostat_name = self.config_dict.get('thermostat_name')
        self.object = self.config_dict.get('object','Thermostat')
        self.payload_dict = {'username': self.username,
                             'password': self.password,
                             'request': '',
                             'object': self.object}
        
        self.parse_config(registry_config_str)

    def parse_config(self, config_dict):
        """
        Given a registry configuration, configure registers and add them to our list of configured registers
        :param config_dict: Registry configuration entry
        """
        # There's nothing to configure, so don't bother
        if config_dict is None:
            return
        # Iterate over the registry configuration entries
        self.name_mapping = {}
        for index, regDef in enumerate(config_dict):
            # Skip lines that have no point name yet
            if not regDef.get('Point Name'):
                raise ValueError("Registry config entry {} did not have a point name or volttron point name".format(
                    index))
            point_name = regDef.get('Point Name')

            read_only = regDef.get('Writable', "").lower() != 'true'
            volttron_point_name = regDef.get('Volttron Point Name')

            if not point_name:
                volttron_point_name = point_name
           
            self.name_mapping[volttron_point_name] = point_name
            description = regDef.get('Notes', '')
            units = regDef.get('Units', None)
            default_value = regDef.get("Default Value", None)
            # Truncate empty string or 0 values to None
            if not default_value:
                default_value = None
            else:
                default_value = default_value.strip()

            type_name = regDef.get("Type", 'string')
            # Make sure the type specified in the configuration is mapped to an actual Python data type
            pytype = type_mapping.get(type_name, str)
            # Create an instance of the register class based on the configuration values
            payload = self.payload_dict

            register = PelicanRegister(
                payload,
                read_only,
                point_name,
                volttron_point_name,
                self.thermostat_name,
                self.url,
                units,
                pytype,
                default_value=default_value,
                description=description
                )
            # Update the register's value if there is a default value provided
            if default_value is not None:
                self.set_default(point_name, default_value)
            # Add the register instance to our list of registers
            self.insert_register(register)

    def get_point(self, volttron_point_name):
        """
        Read the value of the register which matches the passed point name
        :param point_name: The point name of the register the user wishes to read
        :return: the value of the register which matches the point name
        """
        pass
    #     # Determine which register instance is configured for the point we desire
    #     register = self.get_register_by_name(point_name)
    #     # then return that register's state
    #     return register.get_state()

    def _set_point(self, point_name, value):
        """
        Read the value of the register which matches the passed point name
        :param point_name: The point name of the register the user wishes to set
        :param value: The value the user wishes to update the register with
        :return: The value of the register after updates
        """
        # register = self.get_register_by_name(point_name)
        # # We don't want to try to overwrite "write-protected" data so throw an error
        # if register.read_only:
        #     raise IOError("Trying to write to a point configured read only: " + point_name)
        # # set the state, and return the new value
        # return register.set_state(value)
        current = self.client._get("/pods/%s/acStates" % self.uid, limit = 1, fields="acState")['result']
        response = self.client._patch(f'/pods/{self.uid}/acStates/{point_name}',
                json.dumps({'newValue': value}))
        _log.debug(f'new_state of {self.uid} is {response}')
        # return response
        

    def _scrape_all(self): #rather than calling with registry, just doing in one call
        """
        Loop over all of the registers configured for this device, then return a mapping of register name to its value
        :return: Results dictionary of the form {<register point name>: <register value>, ...}
        """
        payload = self.payload_dict.copy()
        payload['value'] = ';'.join(self.name_mapping.values())
        payload['selection'] = f'name:{self.thermostat_name}'
        payload['request'] = 'get'
        r = requests.get(self.url,payload)
        scrape_dict = xmltodict.parse(r.content).get('result')
        
        # remapping names
        results_dict = {}
        for volttron_name, point_name in self.name_mapping.items():
                results_dict[volttron_name] = scrape_dict[point_name]
        
        return results_dict
        
class PelicanRegister(BaseRegister):

    """
    Register class for reading and writing to specific lines of a CSV file
    """
    def __init__(self, payload, read_only, point_name, volttron_point_name, thermostat_name, url, units, pytype,
                 description='', default_value=None):
        # set inherited values
        super(PelicanRegister, self).__init__("byte", read_only, volttron_point_name, units,
                                          description=description)
        self.payload = payload
        self.url = url
        self.thermostat_name= thermostat_name
        self.pytype = pytype

    def get_state(self):
        '''
        Takes a list of points or a single point for a list of thermostats or single thermostat
        and returns their values
        :param points: List of point names or string point name
        :param thermostat_names: List of thermostat names or single thermostat name:
        :returns: ordered dictionary with values for each point
        could optionally add thermostat_serialNo or other identifying information for the selection
        '''
        payload = self.payload_dict.copy()
        payload['value'] = self.point_name
        payload['selection'] = f'name:{self.thermostat_name}'
        payload['request'] = 'get'
        self.get_selection(payload, self.thermostat_name)
        r = requests.get(self.url,payload)
        return xmltodict.parse(r.content).get('result')
    
    def set_state(self, value):
        """
        
        """
        payload = self.payload_dict.copy()
        payload['value'] = f'{self.point_name}:{self.set_type(self.pytype, value)};'
        payload['selection'] = f'name:{self.thermostat_name}'
        payload['request'] = 'set'
        r = requests.get(self.url, payload)
        return dict(xmltodict.parse(r.content))
        

    def set_type(self, pytype, value):
        if pytype is int:
            return int(value)
        elif pytype is float:
            return float(value)
        elif pytype is bool:
            return bool(value)
        elif pytype is str:
            return str(value)