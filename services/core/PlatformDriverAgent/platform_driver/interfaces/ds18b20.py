# -*- coding: utf-8 -*- {{{
# adapted from https://github.com/JeremyMorgan/Hands_on_Internet_of_Things/blob/master/DS18B20/gettemp.py
import os
from platform_driver.interfaces import BaseInterface, BaseRegister, BasicRevert
import logging
import os
import glob
import time

from volttron.platform.agent import utils
from volttron.platform.agent.known_identities import CONFIGURATION_STORE, PLATFORM_DRIVER


_log = logging.getLogger(__name__)
__version__ = "0.5"
type_mapping = {"string": str,
                "int": int,
                "integer": int,
                "float": float,
                "bool": bool,
                "boolean": bool}
class DS18B20Register(BaseRegister):
    """
    Register class for reading temperature from a DS18B20 sensor
    """
    def __init__(self, base_dir,address, read_only, pointName, units,
                 default_value=None, description=''):
        # set inherited values
        super(DS18B20Register, self).__init__("byte", read_only, pointName, units,
                                          description=description)
        # set the path to the CSV this register belongs to
        # Units must be C or F 
        if units != 'C' and units != 'F':
            raise ValueError("Units must be C or F")
        
        self.units = units
        self.device_file = f'{base_dir}/{address}/w1_slave'

    def read_temp_raw(self):
        f = open(self.device_file, 'r')
        lines = f.readlines()
        f.close()
        return lines
    
    def read_temp(self):
        lines = self.read_temp_raw()
        while lines[0].strip()[-3:] != 'YES':
            time.sleep(0.2)
            lines = self.read_temp_raw()
        equals_pos = lines[1].find('t=')
        if equals_pos != -1:
            temp_string = lines[1][equals_pos+2:]
            temp_c = float(temp_string) / 1000.0
            temp_f = temp_c * 9.0 / 5.0 + 32.0
        return temp_c, temp_f
    
    def get_state(self):
        """
        read temperature from the DS18B20 sensor
        """
        if self.units == 'C':
            return self.read_temp()[0]
        elif self.units == 'F':
            return self.read_temp()[1]
        else:
            raise ValueError("Units must be C or F")


class Interface(BasicRevert, BaseInterface):
    """
    "Device Interface" for reading and writing rows of a CSV as a Volttron connected device
    """
    def __init__(self, **kwargs):
        # Configure the base interface
        super(Interface, self).__init__(**kwargs)

    def configure(self, config_dict, registry_config_str):
        """
        Set the Interface attributes from the configurations provided by the Platform Driver, and create the "device" if
        it doesn't already exist
        :param config_dict: Dictionary of configuration values passed from the Platform Driver
        :param registry_config_str: String representation of the registry configuration passed from the Platform Driver
        """
        # Set the CSV interface's necessary attributes from the configuration
        self.base_dir = config_dict.get("base_dir", "/sys/bus/w1/devices/")
        # If the configured path doesn't exist, create the CSV "device" file using the global defaults
        # so that we have something to test against
        if not os.path.isdir(self.base_dir):
            _log.error('DEVICES DO NOT EXIST OR PATH INCORRECT')
            raise(IOError('DEVICES DO NOT EXIST OR PATH INCORRECT'))
        
        self.parse_config(registry_config_str)

    def get_point(self, point_name):
        """
        Read the point with the specific name
        :param point_name: The point name of the register the user wishes to read
        :return: the value of the register which matches the point name
        """
        # Determine which register instance is configured for the point we desire
        register = self.get_register_by_name(point_name)
        # then return that register's state
        return register.get_state()

    def _set_point(self, point_name, value):
        """
        sensors are read only 
        """
        pass

    def _scrape_all(self):
        """
        Loop over all of the registers configured for this device, then return a mapping of register name to its value
        :return: Results dictionary of the form {<register point name>: <register value>, ...}
        """
        # Create a dictionary to hold our results
        result = {}
        # Get all of the registers that are configured for this device, whether they can be written to or not
        read_registers = self.get_registers_by_type("byte", True)
        # For each register, create an entry in the results dictionary with its name as the key and state as the value
        for register in read_registers:
            result[register.point_name] = register.get_state()
        # Return the results
        return result

    def parse_config(self, config_dict):
        """
        
        Given a registry configuration, configure registers and add them to our list of configured registers
        barely modified from existing csv interface
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
            reg_type = type_mapping.get(type_name, str)
            address = regDef.get("Address"):
            if not  address:
                raise ValueError("Registry config entry {} did not have an address".format(
                    index
                ))
            # Create an instance of the register class based on the configuration values
    
            register = DS18B20Register(
                self.base_dir,
                address,
                read_only,
                point_name,
                units,
                reg_type,
                default_value=default_value,
                description=description)
            # Update the register's value if there is a default value provided
            if default_value is not None:
                self.set_default(point_name, register.value)
            # Add the register instance to our list of registers
            self.insert_register(register)