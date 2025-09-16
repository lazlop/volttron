# Daikin driver for VOLTTRON platform
# Designed to interface with Daikin One+ thermostats via the Daikin Integrator API
import requests
import json
import logging
import datetime
from volttron.platform import jsonapi
from volttron.platform.agent import utils
from volttron.platform.vip.agent import RPC
from volttron.platform.agent.known_identities import CONFIGURATION_STORE, PLATFORM_DRIVER
from volttron.utils.persistance import PersistentDict
from platform_driver.interfaces import BaseInterface, BaseRegister, BasicRevert
from volttron.platform.scheduling import cron, periodic

_log = logging.getLogger(__name__)
__version__ = "0.1"
type_mapping = {"string": str,
                "int": int,
                "integer": int,
                "float": float,
                "bool": bool,
                "boolean": bool}

DAIKIN_API_URL_BASE = "https://integrator-api.daikinskyport.com"
DAIKIN_API_URL_AUTH = DAIKIN_API_URL_BASE + "/v1/token"
DAIKIN_API_URL_DEVICES = DAIKIN_API_URL_BASE + "/v1/devices"

class Interface(BasicRevert, BaseInterface):
    """
    Interface implementation for Daikin One+ thermostats
    """

    def __init__(self, **kwargs):
        super(Interface, self).__init__(**kwargs)
        self.daikin_service = None
        self.thermostat_data = None
        _log.debug("DAIKIN DRIVER INSTANTIATED")

    def configure(self, config_dict, registry_config_str):
        """
        Interface configuration callback
        :param config_dict: Driver configuration dictionary
        :param registry_config_str: Driver registry configuration dictionary
        """
        self.config_dict = config_dict
        self.registry_config_str = registry_config_str
        _log.debug("CONFIGURING DAIKIN DRIVER")
        # Extract configuration parameters
        self.api_key = self.config_dict.get('api_key')
        self.integrator_token = self.config_dict.get('integrator_token')
        self.email = self.config_dict.get('email')
        self.thermostat_id = self.config_dict.get('thermostat_id')
        
        creds = {
            'api_key': self.api_key,
            'integrator_token': self.integrator_token,
            'email': self.email
        }
        # Initialize the Daikin service
        self.expires_in = self.vip.rpc.call('daikin.service', 'get_or_create_service', creds).get(timeout=10)
        _log.debug(f"daikin service authenticated, expires in: {self.expires_in}") 

        _log.debug('attempting to get tstat data')
        # self.thermostat_data = self.vip.rpc.call('daikin.service', 'get_thermostat_data', self.thermostat_id).get(timeout=10)
        # _log.debug(f'tstat data: {self.thermostat_data}')

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
            register = DaikinRegister(
                self.daikin_service,
                self.thermostat_id,
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

    def _scrape_all(self):
        """
        Loop over all of the registers configured for this device, then return a mapping of register name to its value
        :return: Results dictionary of the form {<register point name>: <register value>, ...}
        """
        result = {}
        # Get all of the registers that are configured for this device
        read_registers = self.get_registers_by_type("byte", True)
        write_registers = self.get_registers_by_type("byte", False)
        _log.debug(f'SCRAPING TSTAT: {self.thermostat_id}')
        # Refresh the thermostat data once for all registers
        # self.thermostats = self.daikin_service.get_thermostat_list()
        # _log.debug(self.thermostats)
        self.thermostat_data = self.vip.rpc.call('daikin.service', 'get_thermostat_data', self.thermostat_id).get(timeout=20)
            
        print(self.thermostat_data)
        # Return the results
        return self.thermostat_data


class DaikinRegister(BaseRegister):
    """
    Register class for reading and writing to Daikin One+ thermostats
    """
    def __init__(self, daikin_service, thermostat_id, read_only, point_name, units, pytype, 
                 description='', default_value=None):
        # set inherited values
        super(DaikinRegister, self).__init__("byte", read_only, point_name, units,
                                          description=description)
        self.daikin_service = daikin_service
        self.thermostat_id = thermostat_id
        self.pytype = pytype
        
    def get_state(self, thermostat_data=None):
        """
        Get the current value of this register from the thermostat
        :param thermostat_data: Optional pre-fetched thermostat data to avoid redundant API calls
        :return: The current value of the register
        """
        pass
        # if thermostat_data is None:
        #     thermostat_data = self.daikin_service.get_thermostat_data(self.thermostat_id)
            
        # if not thermostat_data:
        #     _log.error(f"Failed to get thermostat data for point {self.point_name}")
        #     return None
            
        # # Extract the value from the thermostat data based on the point name
        # if self.point_name in thermostat_data:
        #     value = thermostat_data[self.point_name]
        #     return self.set_type(self.pytype, value)
        # else:
        #     _log.warning(f"Point {self.point_name} not found in thermostat data")
        #     return None

    def set_state(self, value):
        """
        Set the value of this register on the thermostat
        :param value: the value to set
        :return: The new value of the register
        """
        # Determine which setting to update based on the point name
        # if self.point_name == "heatSetpoint":
        #     result = self.daikin_service.write_setpoints(self.thermostat_id, heatSetpoint=value)
        # elif self.point_name == "coolSetpoint":
        #     result = self.daikin_service.write_setpoints(self.thermostat_id, coolSetpoint=value)
        # elif self.point_name == "mode":
        #     result = self.daikin_service.write_setpoints(self.thermostat_id, mode=value)
        # else:
        #     _log.warning(f"Cannot write to point {self.point_name}, not a supported writable point")
        #     return None
            
        # if result:
        #     return value
        # else:
        #     _log.error(f"Failed to set {self.point_name} to {value}")
        #     return None
        pass

    def set_type(self, pytype, value):
        """
        Convert the value to the specified Python type
        :param pytype: The Python type to convert to
        :param value: The value to convert
        :return: The converted value
        """
        if value is None:
            return None
            
        try:
            if pytype is int:
                return int(value)
            elif pytype is float:
                return float(value)
            elif pytype is bool:
                if isinstance(value, str):
                    return value.lower() in ("yes", "true", "t", "1")
                return bool(value)
            elif pytype is str:
                return str(value)
            else:
                return value
        except (ValueError, TypeError) as e:
            _log.error(f"Error converting {value} to {pytype}: {e}")
            return None
