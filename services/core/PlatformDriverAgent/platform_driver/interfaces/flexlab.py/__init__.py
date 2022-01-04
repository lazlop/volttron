import gevent
import logging
import abc
import sys

from . import service as cps
from . import async_service as async

from .. import BaseInterface, BaseRegister, BasicRevert, DriverInterfaceError
from suds.sudsobject import asdict

_log = logging.getLogger(__name__)

# Somewhere else, suds is set to level Debug. Setting to Info here to not deluge logs.

#suds = logging.getLogger('suds')
#suds.setLevel(logging.INFO)

type_mapping = {"string": str,
                "int": int,
                "integer": int,
                "float": float,
                "bool": bool,
                "boolean": bool,
                "datetime": str,
                "date": str,
                "time": str,
                }

point_name_mapping = {"Status.TimeStamp": "TimeStamp"}

service = {}


class Interface(BasicRevert, BaseInterface):

    def __init__(self, **kwargs):
        super(Interface, self).__init__(**kwargs)

    def configure(self, config_dict, registry_config_str):
        self.point_dict = config_dict.get("")
        """Configure interface for driver.
        :param config_dict: Input from driver config.
        :param registry_config_str: Input from csv file.
        """
        
        self.parse_config(config_dict, registry_config_str)

    def get_point(self, point_name):
        register = self.get_register_by_name(point_name)
        return register.value

    def _set_point(self, point_name, value):
        register = self.get_register_by_name(point_name)
        if register.read_only:
            raise IOError(
                "Trying to write to a point configured read only: {0}".format(point_name))

        register.value = value
        return register.value

    def _scrape_all(self):
        result = {}
        read_registers = self.get_registers_by_type("byte", True)
        write_registers = self.get_registers_by_type("byte", False)
        for register in read_registers + write_registers:
            result[register.point_name] = register.value
        return result

    # def parse_config(self, config_dict, registry_config_str):
    #     """Main method to parse the CSV registry config file."""

    #     if registry_config_str is None:
    #         return

    #     for regDef in registry_config_str:
    #         # Skip lines that have no address yet.
    #         if not regDef['Attribute Name']:
    #             continue

    #         point_name = regDef['Volttron Point Name']
    #         attribute_name = regDef['Attribute Name']
    #         port_num = regDef['Port #']
    #         type_name = regDef.get("Type", 'string')
    #         units = regDef['Units']
    #         read_only = regDef['Writable'].lower() != 'true'
    #         description = regDef.get('Notes', '')
    #         register_name = regDef['Register Name']
    #         default_value = regDef.get('Starting Value', None)
    #         default_value = default_value if default_value != '' else None

    #         data_type = type_mapping.get(type_name, str)

    #         current_module = sys.modules[__name__]
    #         try:
    #             register_type = getattr(current_module, register_name)
    #         except AttributeError:
    #             _log.error('{0} is not a valid register'.format(register_name))
    #             raise DriverInterfaceError('Improperly configured register name')

    #         register = register_type(
    #             read_only,
    #             point_name,
    #             attribute_name,
    #             units,
    #             data_type,
    #             config_dict['stationID'],
    #             default_value=default_value,
    #             description=description,
    #             port_number=port_num,
    #             username=config_dict['username'],
    #             timeout=config_dict['cacheExpiration']
    #         )

    #         self.insert_register(register)

            if default_value is not None:
                self.set_default(point_name, register.value)


def recursive_asdict(d):
    """Convert Suds object into serializable format.

    Credit goes to user plaes as found here:
    http://stackoverflow.com/questions/2412486/serializing-a-suds-object-in-python
    """
    out = {}
    for k, v in asdict(d).items():
        if hasattr(v, '__keylist__'):
            out[k] = recursive_asdict(v)
        elif isinstance(v, list):
            out[k] = []
            for item in v:
                if hasattr(item, '__keylist__'):
                    out[k].append(recursive_asdict(item))
                else:
                    out[k].append(item)
        else:
            out[k] = v
    return out


class FlexlabRegister(BaseRegister):
    
    def __init__(self, csv_path, read_only, pointName, units, reg_type,
                 default_value=None, description=''):
        super(FlexlabRegister, self).__init__("byte", read_only, point_name, units, description=description)
        self.data_type = data_type
        self.station_id = station_id
        self.port = int(port_number) if port_number else None
        self.attribute_name = attribute_name
        self.username = username
        self.timeout = timeout

        if default_value:
            self.value = default_value

    @abc.abstractmethod
    def value(self):
        pass

    @abc.abstractmethod
    def value(self, x):
        pass

    @staticmethod
    def sanitize_output(data_type, value):
        if value == "None":
            return None
        elif value is None:
            return None
        else:
            try:
                return data_type(value)
            except ValueError:
                _log.error("{0} cannot be cast to {1}".format(value, data_type))
                return None

    def read_only_check(self):
        if self.read_only:
            raise IOError("Trying to write to a point configured read only: {0}".format(self.attribute_name))
        return True

    def get_register(self, result, method, port_flag=True):
        """Gets correct register from API response.

        :param result: API result from which to grab register value.
        :param method: Name of Chargepoint API call that was made.
        :param port_flag: Flag indicating whether or not Port-level parameters can be used. GetAlarms and
        GetChargingSessionData methods use ports in their queries, but have a different reply structure than other
        API method calls.

        :return: Correct register value cast to appropriate python type. Returns None if there is an error.
        """
        try:
            value = getattr(result, self.attribute_name)(self.port)[0] \
                if port_flag \
                else getattr(result, self.attribute_name)(None)[0]
            return self.sanitize_output(self.data_type, value)
        except cps.CPAPIException as exception:
            if exception._responseCode not in ['153']:
                _log.error('{0} did not execute for station {1}.'.format(method, self.station_id))
                _log.error(str(exception))
            return None


#   For all sub-classes of ChargepointRegister, they have an attribute_list and a writeable_list. These are master lists
#   to validate all CSV register configs. Any given subclass of ChargepointRegister may only have attributes listed
#   in their corresponding attriute list, and similarly, only those attributes included in the writeable list are
#   permitted to have read/write access. Of Note: Most ChargepointRegister subclasses do not have any writeable
#   attributes


class StationRegister(ChargepointRegister):
    """Register designated for all attributes returned from the Chargepoint API getStations call.

    Input parameters are the same as parent ChargepointRegister class. No attribute in this register is writeable.
    """

    attribute_list = ['stationID', 'stationManufacturer', 'stationModel', 'portNumber', 'stationName', 'stationMacAddr',
                      'stationSerialNum', 'Address', 'City', 'State', 'Country', 'postalCode', 'Lat', 'Long', 'Level',
                      'Reservable', 'Mode', 'Voltage', 'Current', 'Power', 'numPorts', 'Type', 'startTime', 'endTime',
                      'minPrice', 'maxPrice', 'unitPricePerHour', 'unitPricePerSession', 'unitPricePerKWh', 'orgID',
                      'unitPriceForFirst', 'unitPricePerHourThereafter', 'sessionTime', 'Description', 'mainPhone',
                      'organizationName', 'sgID', 'sgName', 'currencyCode', 'Connector']
    writeable_list = []

    def __init__(self, read_only, point_name, attribute_name, units, data_type, station_id,
                 default_value=None, description='', port_number=None, username=None, timeout=0):
        super(StationRegister, self).__init__(read_only, point_name, attribute_name, units, data_type, station_id,
                                              default_value, description, port_number, username, timeout)
        if attribute_name not in StationRegister.attribute_list:
            raise DriverInterfaceError('{0} cannot be assigned to this register.'.format(attribute_name))
        if not read_only and attribute_name not in StationRegister.writeable_list:
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(attribute_name))

    @property
    def value(self):
        global service
        method = service[self.username].getStations
        result = async.CPRequest.request(method, self.timeout, stationID=self.station_id)
        result.wait()
        return self.get_register(result.value, method)

    @value.setter
    def value(self, x):
        # No points defined by StationRegister are writeable.
        if self.read_only_check():
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(self.attribute_name))


class LoadRegister(ChargepointRegister):
    """Register designated for all attributes returned from the Chargepoint API getLoad call.

    Input parameters are the same as parent ChargepointRegister class.

    Writeable attributes (Note, if either allowedLoad or percentShed are set, the other will be set to None. In
    addition, shedState will be set to 1 or True):

    allowedLoad: Amount of load to shed in an absolute value.  Limits charging to x kW.
    percentShed: Percent of load to shed.  Limits charging to x% of load.
    shedState: Only accepts a write-value of 0 or False. This indicates that the Chargepoint station should be cleared
    of any load shed constraints
    """

    attribute_list = ['portLoad', 'allowedLoad', 'percentShed', 'shedState']
    writeable_list = ['allowedLoad', 'percentShed', 'shedState']

    def __init__(self, read_only, point_name, attribute_name, units, data_type, station_id,
                 default_value=None, description='', port_number=None, username=None, timeout=0):
        super(LoadRegister, self).__init__(read_only, point_name, attribute_name, units, data_type, station_id,
                                           default_value, description, port_number, username, timeout)
        if attribute_name not in LoadRegister.attribute_list:
            raise DriverInterfaceError('{0} cannot be assigned to this register.'.format(attribute_name))
        if not read_only and attribute_name not in LoadRegister.writeable_list:
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(attribute_name))

    @property
    def value(self):
        global service
        method = service[self.username].getLoad
        result = async.CPRequest.request(method, self.timeout, stationID=self.station_id)
        result.wait()
        return self.get_register(result.value, method)

    @value.setter
    def value(self, x):
        """Makes Chargepoint API call to shedLoad or clearShedState.

        If shedLoad is written to (and is written a value of 0), clearShedState is called. Otherwise, if shedLoad has a
        non-zero value, an error will be logged and None will be returned. On a successful write, the value given as
        input will be returned

        :param x: The value at which to set the register.
        :return: If API call is successful, will return x.
        """
        if self.read_only_check():
            global service
            try:
                value = self.data_type(x)
            except ValueError:
                _log.error("{0} cannot be cast to {1}".format(x, self.data_type))
                return

            kwargs = {'stationID': self.station_id}
            if self.attribute_name == 'shedState' and not value:
                method = service[self.username].clearShedState
                result = async.CPRequest.request(method, 0, stationID=self.station_id)
            elif self.attribute_name == 'shedState':
                _log.error('shedState may only be written with value 0. If you want to shedLoad, write to '
                           'allowedLoad or percentShed')
                return
            else:
                method = service[self.username].shedLoad
                kwargs[self.attribute_name] = value
                if self.port:
                    kwargs['portNumber'] = self.port
                result = async.CPRequest.request(method, 0, **kwargs)

            result.wait()
            if result.value.responseCode != "100":
                _log.error('{0} did not execute for station {1}. Parameters: {2}'
                           .format(method, self.station_id, kwargs))
                _log.error('{0} : {1}'.format(result.value.responseCode, result.value.responseText))


class AlarmRegister(ChargepointRegister):
    """Register designated for all attributes returned from the Chargepoint API getAlarms call.

    Input parameters are the same as parent ChargepointRegister class.

    Readable attributes:
    alarmType and alarmTime: Return the most recent alarm registered for the given station.  If a port is defined for
    the register, only alarms ascribed to the port will be returned. If value is None, then there are no active alarms
    describing the station and/or port (depending on config).

    Writeable attributes:

    clearAlarms: Only accepts a write-value of 1 or True. This indicates that the Chargepoint station should be cleared
    of any alarms.  If a point is defined for the register, only alarms ascribed to the port will be cleared.  This
    value, when read, will always return None, as it does not exist as a returnable Chargepoint attribute.
    """

    attribute_list = ['alarmType', 'alarmTime', 'clearAlarms']
    writeable_list = ['clearAlarms']

    def __init__(self, read_only, point_name, attribute_name, units, data_type, station_id,
                 default_value=None, description='', port_number=None, username=None, timeout=0):
        super(AlarmRegister, self).__init__(read_only, point_name, attribute_name, units, data_type, station_id,
                                            default_value, description, port_number, username, timeout)
        if attribute_name not in AlarmRegister.attribute_list:
            raise DriverInterfaceError('{0} cannot be assigned to this register.'.format(attribute_name))
        if not read_only and attribute_name not in AlarmRegister.writeable_list:
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(attribute_name))

    @property
    def value(self):
        global service

        if self.attribute_name == 'clearAlarms':
            return False
        method = service[self.username].getAlarms
        kwargs = {'stationID': self.station_id}
        if self.port:
            kwargs['portNumber'] = self.port

        result = async.CPRequest.request(method, self.timeout, **kwargs)
        result.wait()
        return self.get_register(result.value, method, False)

    @value.setter
    def value(self, x):
        """Makes Chargepoint API call to clear alarm registers.

        Only writeable register is clearAlarms, and it should only be writable as 1. If there are no alarms to be
        cleared, the API will return an error code. This will be excepted, logged, and returned as None.

        :param x: The value at which to set the register.
        :return: If API call is successful, will return x. If there is an error (or there are no alarms to clear) this
        will return None.
        """
        if self.read_only_check():
            global service
            try:
                value = self.data_type(x)
            except ValueError:
                _log.error("{0} cannot be cast to {1}".format(x, self.data_type))
                return

            if self.attribute_name == 'clearAlarms' and value:
                kwargs = {'stationID': self.station_id}
                method = service[self.username].clearAlarms
                result = async.CPRequest.request(method, 0, **kwargs)

                result.wait()
                if result.value.responseCode not in ['100', '153']:
                    _log.error('{0} did not execute for station {1}. Parameters: {2}'
                               .format(method, self.station_id, kwargs))
                    _log.error('{0} : {1}'.format(result.value.responseCode, result.value.responseText))
            else:
                _log.info('clearAlarms may only be given a value of 1. Instead, it was given a value of {0}.'.format(x))


class ChargingSessionRegister(ChargepointRegister):
    """Register designated for all attributes returned from the Chargepoint API getChargingSessions call.

    Input parameters are the same as parent ChargepointRegister class. No attribute in this register is writeable.
    """

    attribute_list = ['sessionID', 'startTime', 'endTime', 'Energy', 'rfidSerialNumber', 'driverAccountNumber',
                      'driverName']
    writeable_list = []

    def __init__(self, read_only, point_name, attribute_name, units, data_type, station_id,
                 default_value=None, description='', port_number=None, username=None, timeout=0):
        super(ChargingSessionRegister, self).__init__(read_only, point_name, attribute_name, units, data_type,
                                                      station_id, default_value, description, port_number, username,
                                                      timeout)
        if attribute_name not in ChargingSessionRegister.attribute_list:
            raise DriverInterfaceError('{0} cannot be assigned to this register.'.format(attribute_name))
        if not read_only and attribute_name not in ChargingSessionRegister.writeable_list:
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(attribute_name))

    @property
    def value(self):
        global service
        method = service[self.username].getChargingSessionData
        result = async.CPRequest.request(method, self.timeout, stationID=self.station_id)
        result.wait()

        # Of Note, due to API limitations, port number is ignored for these calls
        return self.get_register(result.value, method, False)

    @value.setter
    def value(self, x):
        # No points defined by ChargingSessionRegister are writeable.
        if self.read_only_check():
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(self.attribute_name))


class StationStatusRegister(ChargepointRegister):
    """Register designated for all attributes returned from the Chargepoint API getStationStatus call.

    Input parameters are the same as parent ChargepointRegister class. No attribute in this register is writeable.
    """

    attribute_list = ['Status', 'TimeStamp']
    writeable_list = []

    def __init__(self, read_only, point_name, attribute_name, units, data_type, station_id,
                 default_value=None, description='', port_number=None, username=None, timeout=0):
        super(StationStatusRegister, self).__init__(read_only, point_name, attribute_name, units, data_type, station_id,
                                                    default_value, description, port_number, username, timeout)
        if attribute_name not in StationStatusRegister.attribute_list:
            raise DriverInterfaceError('{0} cannot be assigned to this register.'.format(attribute_name))
        if not read_only and attribute_name not in StationStatusRegister.writeable_list:
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(attribute_name))

    @property
    def value(self):
        global service
        method = service[self.username].getStationStatus
        result = async.CPRequest.request(method, self.timeout, self.station_id)
        result.wait()
        return self.get_register(result.value, method)

    @value.setter
    def value(self, x):
        # No points defined by StationStatusRegister are writeable.
        if self.read_only_check():
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(self.attribute_name))


class StationRightsRegister(ChargepointRegister):
    """Register designated for all attributes returned from the Chargepoint API getStationRights call.

    Input parameters are the same as parent ChargepointRegister class. No attribute in this register is writeable.

    Unlike any other ChargepointRegister subclasses, the stationRightsProfile is of type 'dictionary.' This calls the
    global method recursive_asdict, which takes the returned SUDS object and converts it, recursively, into a python
    dictionary.  As such, this register does not go through the parent class get_register method to return its value.
    """

    attribute_list = ['stationRightsProfile']
    writeable_list = []

    def __init__(self, read_only, point_name, attribute_name, units, data_type, station_id,
                 default_value=None, description='', port_number=None, username=None, timeout=0):
        super(StationRightsRegister, self).__init__(read_only, point_name, attribute_name, units, data_type, station_id,
                                                    default_value, description, port_number, username, timeout)
        if attribute_name not in StationRightsRegister.attribute_list:
            raise DriverInterfaceError('{0} cannot be assigned to this register.'.format(attribute_name))
        if not read_only and attribute_name not in StationRightsRegister.writeable_list:
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(attribute_name))

    @property
    def value(self):
        global service
        method = service[self.username].getStationRights
        result = async.CPRequest.request(method, self.timeout, stationID=self.station_id)
        result.wait()

        # Note: this does not go through get_register, as it is of a unique type, 'dictionary.'
        rights_dict = {}
        try:
            for profile in result.value.rights:
                rights_dict[profile["sgID"]] = recursive_asdict(profile)
            return rights_dict

        except cps.CPAPIException as exception:
            _log.error(str(exception))
            return None

    @value.setter
    def value(self, x):
        # No points defined by StationRightsRegister are writeable.
        if self.read_only_check():
            raise DriverInterfaceError('{0} cannot be configured as a writeable register'.format(self.attribute_name))
