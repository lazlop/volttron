'''Right now designed to be one driver for each thermostat.
(Could be one driver for every thermostat if we want)
for some reason in BaseRegister class,
the python class for everything is set to int. Reg_types are bit or byte
'''
import logging
import urllib, datetime
from xml.etree import ElementTree as ET

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
        self.device = self.config_dict.get("device")
        # self.payload_dict = {'api_key': self.api_key,
        #                      'uid': self.uid,
        #                      'value': ''}
        # self.client = SensiboClientAPI(self.api_key, self.url)
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
        self.mapping = {}
        for index, regDef in enumerate(config_dict):
            # Skip lines that have no point name yet
            self.mapping[regDef.get('Volttron Point Name')] = regDef.get('Point Name')
        print(self.mapping)
    #         if not regDef.get('Point Name'):
    #             continue
    # # Extract the values of the configuration, and format them for our purposes
    #         read_only = regDef.get('Writable', "").lower() != 'true'
    #         point_name = regDef.get('Volttron Point Name')
    #         if not point_name:
    #             point_name = regDef.get("Point Name")
    #         if not point_name:
    #         # We require something we can use as a name for the register, so don't try to create a register without
    #         # the name
    #             raise ValueError("Registry config entry {} did not have a point name or volttron point name".format(
    #                 index))
    #         description = regDef.get('Notes', '')
    #         units = regDef.get('Units', None)
    #         default_value = regDef.get("Default Value", "").strip()
    #         # Truncate empty string or 0 values to None
    #         if not default_value:
    #             default_value = None
    #         type_name = regDef.get("Type", 'string')
    #         # Make sure the type specified in the configuration is mapped to an actual Python data type
    #         pytype = type_mapping.get(type_name, str)
    #         # Create an instance of the register class based on the configuration values
    #         payload = self.payload_dict
    #         payload['value'] = point_name
    #         register = PelRegister(
    #             self.url,
    #             payload,
    #             read_only,
    #             point_name,
    #             units,
    #             pytype,
    #             default_value=default_value,
    #             description=description)
    #         # Update the register's value if there is a default value provided
    #         if default_value is not None:
    #             self.set_default(point_name, register.value)
    #         # Add the register instance to our list of registers
    #         self.insert_register(register)
    

    def get_point(self, point_name):
        """
        Read the value of the register which matches the passed point name
        :param point_name: The point name of the register the user wishes to read
        :return: the value of the register which matches the point name
        """
        pass

    def _set_point(self, point_name, value):
        """
        Read the value of the register which matches the passed point name
        :param point_name: The point name of the register the user wishes to set
        :param value: The value the user wishes to update the register with
        :return: The value of the register after updates
        """
        pass
        
    def get_min_avg(self):
        url = f'https://{self.device}.egaug.es/cgi-bin/egauge-show?m&n=2'
        tree = ET.parse(urllib.request.urlopen(url)).getroot()
        data = list(iter(tree))[0]

        ts = int(data.attrib['time_stamp'],16)
        time = datetime.datetime.fromtimestamp(ts)
        data_dict = {}
        row_0 = list(data.iter('r'))[0]
        row_0_vals = [int(i.text) for i in row_0.iter('c')]
        row_1 = list(data.iter('r'))[1]
        row_1_vals = [int(i.text) for i in row_1.iter('c')]
        for i, row in enumerate(data.iter('cname')):
            # Doing absolute value for now, since all values should be positive (and show as positive in Egauge UI)
            data_dict[row.text] = abs((row_0_vals[i] - row_1_vals[i])/60)
        return time, data_dict

    def _scrape_all(self): #rather than calling with registry, just doing in one call
        """
        Loop over all of the registers configured for this device, then return a mapping of register name to its value
        :return: Results dictionary of the form {<register point name>: <register value>, ...}
        """
        ts, avg_dict = self.get_min_avg()
        result = {}
        for k,v in self.mapping.items():
            result[k] = avg_dict[v]
        result['timeMeasured'] = utils.format_timestamp(ts)
        print(result)
        return result
    
class PelRegister(BaseRegister):

    """
    Register class for reading and writing to specific lines of a CSV file
    """
    def __init__(self, url, payload, read_only, pointName, units, pytype,
                 description='', default_value=None):
        # set inherited values
        super(PelRegister, self).__init__("byte", read_only, pointName, units,
                                          description=description)
        self.payload = payload
        self.url = url
        self.pytype = pytype

#     def get_state(self):
#         """
#         Iterate over the CSV, find the row where the Point Name matches the name of this register
#         :return: The Point Value of the row that matches the register
#         """
#         measurement = s.pod_measurement(uid)[0][self]
#         m_dict = {'time_measured': measurement['time']['time'],
#                  'temperature': measurement['temperature']}
        
#         return self.set_type(self.pytype, value)

    # def set_state(self, value):
    #     """
    #     Set the value of the row this register represents in the CSV file
    #     :param value: the value to set in the row
    #     :return: The new value of the row
    #     """
    #     payload = self.payload
    #     payload['value'] = self.point_name + ':' + str(value)
    #     payload['request'] = 'set'
    #     print(payload)
    #     r = requests.get(self.url, self.payload)
    #     return dict(xmltodict.parse(r.content))

    def set_type(self, pytype, value):
        if pytype is int:
            return int(value)
        elif pytype is float:
            return float(value)
        elif pytype is bool:
            return bool(value)
        elif pytype is str:
            return str(value)