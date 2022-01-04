#Right now designed to be one driver for each thermostat. (Could be one driver for every thermostat if we want)
#for some reason in BaseRegister class, the python class for everything is set to int. Reg_types are bit or byte
import requests
import xmltodict
import datetime
import gevent
import grequests
import logging
import requests
from requests.exceptions import HTTPError
from requests.packages.urllib3.connection import ConnectionError, NewConnectionError

from volttron.platform import jsonapi
from volttron.platform.agent import utils
from volttron.platform.agent.known_identities import CONFIGURATION_STORE, PLATFORM_DRIVER
from volttron.utils.persistance import PersistentDict
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
        self.url = self.config_dict.get("url","https://lbl-building-90c.officeclimatecontrol.net/api.cgi")
        self.username = self.config_dict.get('username')
        self.password = self.config_dict.get('password')
        self.object = self.config_dict.get('object')
        self.thermostat_name = self.config_dict.get('thermostat_name')
        self.payload_dict = {'username':self.username,
            'password':self.password ,
            'request': '',
            'object': 'Thermostat',
            'selection': 'name:'+self.thermostat_name,
            'value': ''}
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
        for index, regDef in enumerate(config_dict):
            # Skip lines that have no point name yet
            if not regDef.get('Point Name'):
                continue
            # Extract the values of the configuration, and format them for our purposes
            read_only = regDef.get('Writable', "").lower() != 'true'
            point_name = regDef.get('Volttron Point Name')
            if not point_name:
                point_name = regDef.get("Point Name")
            if not point_name:
                # We require something we can use as a name for the register, so don't try to create a register without
                # the name
                raise ValueError("Registry config entry {} did not have a point name or volttron point name".format(
                    index))
            description = regDef.get('Notes', '')
            units = regDef.get('Units', None)
            default_value = regDef.get("Default Value", "").strip()
            # Truncate empty string or 0 values to None
            if not default_value:
                default_value = None
            type_name = regDef.get("Type", 'string')
            # Make sure the type specified in the configuration is mapped to an actual Python data type
            pytype = type_mapping.get(type_name, str)
            # Create an instance of the register class based on the configuration values
            payload = self.payload_dict
            payload['value'] = point_name
            register = PelRegister(
                self.url,
                payload,
                read_only,
                point_name,
                units,
                pytype,
                default_value=default_value,
                description=description)
            # Update the register's value if there is a default value provided
            if default_value is not None:
                self.set_default(point_name, register.value)
            # Add the register instance to our list of registers
            self.insert_register(register)

    def get_point(self, point_name):
        """
        Read the value of the register which matches the passed point name
        :param point_name: The point name of the register the user wishes to read
        :return: the value of the register which matches the point name
        """
        # Determine which register instance is configured for the point we desire
        register = self.get_register_by_name(point_name)
        # then return that register's state
        return register.get_state()

    def _set_point(self, point_name, value):
        """
        Read the value of the register which matches the passed point name
        :param point_name: The point name of the register the user wishes to set
        :param value: The value the user wishes to update the register with
        :return: The value of the register after updates
        """
        register = self.get_register_by_name(point_name)
        # We don't want to try to overwrite "write-protected" data so throw an error
        if register.read_only:
            raise IOError("Trying to write to a point configured read only: " + point_name)
        # set the state, and return the new value
        return register.set_state(value)

    def _scrape_all(self): #rather than calling with registry, just doing in one call
        """
        Loop over all of the registers configured for this device, then return a mapping of register name to its value
        :return: Results dictionary of the form {<register point name>: <register value>, ...}
        """
        # Get all of the registers that are configured for this device, whether they can be written to or not
        # payload= self.payload_dict
        # values = ''
        # for index, regDef in enumerate(self.registry_config_str):
        #     values = values + ';' + regDef.get("Point Name")
        # payload['value'] = values
        # payload['request'] = 'get'
        # print(payload)
        # r = requests.get(self.url,payload)
        # Return the results
        #return dict(xmltodict.parse(r.content).get('result').get('Thermostat'))
        result = {}
        # Get all of the registers that are configured for this device, whether they can be written to or not
        read_registers = self.get_registers_by_type("byte", True)
        write_registers = self.get_registers_by_type("byte", False)
        # For each register, create an entry in the results dictionary with its name as the key and state as the value
        for register in read_registers + write_registers:
            result[register.point_name] = register.get_state()
        # Return the results
        return result

class PelRegister(BaseRegister):
    """
    Register class for reading and writing to specific lines of a CSV file
    """
    def __init__(self, url, payload, read_only, pointName, units, pytype, description = '',
                 default_value=None):
        # set inherited values
        super(PelRegister, self).__init__("byte", read_only, pointName, units,
                                          description=description)
        self.payload = payload
        self.url = url
        self.pytype = pytype
    def get_state(self):
        """
        Iterate over the CSV, find the row where the Point Name matches the name of this register
        :return: The Point Value of the row that matches the register
        """
        payload = self.payload
        payload['value'] = self.point_name
        payload['request'] = 'get'
        print(payload)
        r = requests.get(self.url, self.payload)
        print(xmltodict.parse(r.content))
        value = xmltodict.parse(r.content).get('result').get('Thermostat').get(self.point_name)
        return self.set_type(self.pytype, value)

    def set_state(self, value):
        """
        Set the value of the row this register represents in the CSV file
        :param value: the value to set in the row
        :return: The new value of the row
        """
        payload = self.payload
        payload['value'] = self.point_name + ':' + str(value)
        payload['request'] = 'set'
        print(payload)
        r = requests.get(self.url, self.payload)
        return dict(xmltodict.parse(r.content))

    def set_type(self,pytype,value):
        if pytype is int:
            return int(value)
        elif pytype is float:
            return float(value)
        elif pytype is bool:
            return bool(value)
        elif pytype is str:
            return str(value)

