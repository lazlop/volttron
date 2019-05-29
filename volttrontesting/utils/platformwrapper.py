import ConfigParser as configparser
from datetime import datetime
import json
import logging
import os
import psutil
import shutil
import sys
import tempfile
import time
import re
from contextlib import closing
from os.path import dirname
from subprocess import CalledProcessError

import gevent
import gevent.subprocess as subprocess
import requests
from agent_additions import (add_volttron_central,
                             add_volttron_central_platform)
from gevent.fileobject import FileObject
from gevent.subprocess import Popen
from volttron.platform import packaging
from volttron.platform.agent.known_identities import MASTER_WEB, CONTROL
from volttron.platform.certs import Certs
from volttron.platform.agent import utils
from volttron.platform.agent.utils import (strip_comments,
                                           load_platform_config,
                                           store_message_bus_config)
from volttron.platform.aip import AIPplatform
from volttron.platform.auth import (AuthFile, AuthEntry,
                                    AuthFileEntryAlreadyExists)
from volttron.platform.keystore import KeyStore, KnownHostsStore
from volttron.platform.vip.agent import Agent
from volttron.platform.vip.agent.connection import Connection
from volttrontesting.utils.utils import get_rand_http_address
from volttrontesting.utils.utils import get_rand_tcp_address
from volttron.platform.agent import json as jsonapi
from volttrontesting.fixtures.rmq_test_setup import create_rmq_volttron_setup
from volttron.platform.agent.utils import execute_command, execute_command_p
from volttron.utils.rmq_setup import start_rabbit, stop_rabbit


utils.setup_logging()
_log = logging.getLogger(__name__)

RESTRICTED_AVAILABLE = False

# Change the connection timeout to default to 5 seconds rather than the default
# of 30 secondes
DEFAULT_TIMEOUT = 5

try:
    from volttron.restricted import (auth, certs)

    RESTRICTED_AVAILABLE = True

except ImportError:
    RESTRICTED_AVAILABLE = False
    auth = None
    certs = None

# Filenames for the config files which are created during setup and then
# passed on the command line
TMP_PLATFORM_CONFIG_FILENAME = "config"

# Used to fill in TWISTED_CONFIG template
TEST_CONFIG_FILE = 'base-platform-test.json'

PLATFORM_CONFIG_RESTRICTED = """
mobility-address = {mobility-address}
control-socket = {tmpdir}/run/control
resource-monitor = {resource-monitor}
"""

TWISTED_CONFIG = """
[report 0]
ReportDeliveryLocation = {smap-uri}/add/{smap-key}

[/]
type = Collection
Metadata/SourceName = {smap-source}
uuid = {smap-uuid}

[/datalogger]
type = volttron.drivers.data_logger.DataLogger
interval = 1

"""

UNRESTRICTED = 0
VERIFY_ONLY = 1
RESOURCE_CHECK_ONLY = 2
RESTRICTED = 3

MODES = (UNRESTRICTED, VERIFY_ONLY, RESOURCE_CHECK_ONLY, RESTRICTED)

VOLTTRON_ROOT = os.environ.get("VOLTTRON_ROOT")
if not VOLTTRON_ROOT:
    VOLTTRON_ROOT = dirname(dirname(dirname(os.path.realpath(__file__))))

if os.environ.get('CI', None) is None:
    VSTART = os.path.join(VOLTTRON_ROOT, "env/bin/volttron")
    VCTRL = os.path.join(VOLTTRON_ROOT, "env/bin/volttron-ctl")
    TWISTED_START = os.path.join(VOLTTRON_ROOT, "env/bin/twistd")
else:
    VSTART = "volttron"
    VCTRL = "volttron-ctl"
    TWISTED_START = "twistd"

SEND_AGENT = "send"

RUN_DIR = 'run'
PUBLISH_TO = RUN_DIR + '/publish'
SUBSCRIBE_TO = RUN_DIR + '/subscribe'


class PlatformWrapperError(StandardError):
    pass


def build_vip_address(dest_wrapper, agent):
    """
    Create a usable vip address with zap parameters embedded in the uri.

    :param dest_wrapper:PlatformWrapper:
        The destination wrapper instance that the agent will be attempting to
        connect to.
    :param agent:Agent
        The agent that is being used to make the connection to dest_wrapper
    :return:
    """
    return "{}:?serverkey={}&publickey={}&secretkey={}".format(
        dest_wrapper.vip_address, dest_wrapper.publickey,
        agent.core.publickey, agent.core.secretkey
    )


def start_wrapper_platform(wrapper, with_http=False, with_tcp=True,
                           volttron_central_address=None,
                           volttron_central_serverkey=None,
                           add_local_vc_address=False):
    """ Customize easily customize the platform wrapper before starting it.
    """
    # Please note, if 'with_http'==True, then instance name needs to be provided
    assert not wrapper.is_running()

    # Will returen https if messagebus rmq
    bind_address = get_rand_http_address(wrapper.messagebus == 'rmq') if with_http else None
    vc_http = bind_address
    vc_tcp = get_rand_tcp_address() if with_tcp else None

    if add_local_vc_address:
        ks = KeyStore(os.path.join(wrapper.volttron_home, 'keystore'))
        ks.generate()
        if wrapper.messagebus == 'rmq':
            volttron_central_address = vc_http
        else:
            volttron_central_address = vc_tcp
            volttron_central_serverkey = ks.public

    wrapper.startup_platform(vip_address=vc_tcp,
                             bind_web_address=bind_address,
                             volttron_central_address=volttron_central_address,
                             volttron_central_serverkey=volttron_central_serverkey)
    if with_http:
        discovery = "{}/discovery/".format(vc_http)
        response = requests.get(discovery)
        assert response.ok

    assert wrapper.is_running()


class PlatformWrapper:
    def __init__(self, messagebus=None, ssl_auth=False, instance_name=None, remote_platform_ca=None):
        """ Initializes a new VOLTTRON instance

        Creates a temporary VOLTTRON_HOME directory with a packaged directory
        for agents that are built.

        :param messagebus: rmq or zmq
        :param ssl_auth: if message_bus=rmq, authenticate users if True
        """

        # This is hopefully going to keep us from attempting to shutdown
        # multiple times.  For example if a fixture calls shutdown and a
        # lower level fixture calls shutdown, this won't hang.
        self._instance_shutdown = False

        self.volttron_home = tempfile.mkdtemp()
        self.packaged_dir = os.path.join(self.volttron_home, "packaged")
        os.makedirs(self.packaged_dir)

        # in the context of this platform it is very important not to
        # use the main os.environ for anything.
        self.env = {
            'VOLTTRON_HOME': self.volttron_home,
            'PACKAGED_DIR': self.packaged_dir,
            'DEBUG_MODE': os.environ.get('DEBUG_MODE', ''),
            'DEBUG': os.environ.get('DEBUG', ''),
            'SKIP_CLEANUP': os.environ.get('SKIP_CLEANUP', ''),
            'PATH': VOLTTRON_ROOT + ':' + os.environ['PATH'],
            # RABBITMQ requires HOME env set
            'HOME': os.environ.get('HOME'),
            # Elixir (rmq pre-req) requires locale to be utf-8
            'LANG': "en_US.UTF-8",
            'LC_ALL': "en_US.UTF-8",
            'PYTHONDONTWRITEBYTECODE': '1'
        }
        self.volttron_root = VOLTTRON_ROOT

        volttron_exe = execute_command(['which', 'volttron']).strip()

        assert os.path.exists(volttron_exe)
        self.python = os.path.join(os.path.dirname(volttron_exe), 'python')
        if not os.path.exists(self.python):
            self.python = execute_command(['which', 'python']).strip()
            assert os.path.exists(self.python)

        # By default no web server should be started.
        self.bind_web_address = None
        self.discovery_address = None
        self.jsonrpc_endpoint = None
        self.volttron_central_address = None
        self.volttron_central_serverkey = None
        self.instance_name = instance_name
        self.serverkey = None

        # The main volttron process will be under this variable
        # after startup_platform happens.
        self.p_process = None

        self.started_agent_pids = []
        self.local_vip_address = None
        self.vip_address = None
        self.logit('Creating platform wrapper')

        # Added restricted code properties
        self.certsobj = None

        # Control whether the instance directory is cleaned up when shutdown.
        # if the environment variable DEBUG is set to a True value then the
        # instance is not cleaned up.
        self.skip_cleanup = False

        # This is used as command line entry replacement.  Especially working
        # with older 2.0 agents.
        self.opts = None

        keystorefile = os.path.join(self.volttron_home, 'keystore')
        self.keystore = KeyStore(keystorefile)
        self.keystore.generate()
        self.messagebus = messagebus if messagebus else 'zmq'
        self.ssl_auth = ssl_auth
        self.instance_name = instance_name
        if not self.instance_name:
            self.instance_name = os.path.basename(self.volttron_home)

        # Set the VOLTTRON_HOME for this process...note this
        # seems tricky but this platform should start up before
        # the rest so it should work out ok.
        os.environ['VOLTTRON_HOME'] = self.volttron_home

        # Writes the main volttron config file for this instance.
        store_message_bus_config(self.messagebus, self.instance_name)

        # Necessary for rabbitmq to shutdown the correct rmq messagebus.
        # This is set during the startup_platform method.
        self.rabbitmq_config_obj = None
        self.remote_platform_ca = remote_platform_ca
        self.requests_ca_bundle = None
        self.dynamic_agent = None

        self.debug_mode = self.env.get('DEBUG_MODE', False)
        if not self.debug_mode:
            self.debug_mode = self.env.get('DEBUG', False)
        self.skip_cleanup = self.env.get('SKIP_CLEANUP', False)

        self._web_admin_api = None

    @property
    def web_admin_api(self):
        return self._web_admin_api

    def logit(self, message):
        print('{}: {}'.format(self.volttron_home, message))

    def allow_all_connections(self):
        """ Add a /.*/ entry to the auth.json file.
        """
        entry = AuthEntry(credentials="/.*/")
        authfile = AuthFile(self.volttron_home + "/auth.json")
        try:
            authfile.add(entry)
        except AuthFileEntryAlreadyExists:
            pass

    def get_agent_identity(self, agent_uuid):
        path = os.path.join(self.volttron_home, 'agents/{}/IDENTITY'.format(agent_uuid))
        with open(path) as f:
            identity = f.read().strip()
        return identity

    def get_agent_by_identity(self, identity):
        for agent in self.list_agents():
            if agent.get('identity') == identity:
                return agent

    def build_connection(self, peer=None, address=None, identity=None,
                         publickey=None, secretkey=None, serverkey=None,
                         capabilities=[], **kwargs):
        self.logit('Building connection to {}'.format(peer))
        self.allow_all_connections()

        if address is None:
            self.logit(
                'Default address was None so setting to current instances')
            address = self.vip_address
            serverkey = self.serverkey
        if serverkey is None:
            self.logit("serverkey wasn't set but the address was.")
            raise Exception("Invalid state.")

        if publickey is None or secretkey is None:
            self.logit('generating new public secret key pair')
            keyfile = tempfile.mktemp(".keys", "agent", self.volttron_home)
            keys = KeyStore(keyfile)
            keys.generate()
            publickey = keys.public
            secretkey = keys.secret
            entry = AuthEntry(capabilities=capabilities,
                              comments="Added by test",
                              credentials=keys.public)
            file = AuthFile(self.volttron_home + "/auth.json")
            file.add(entry)

        conn = Connection(address=address, peer=peer, publickey=publickey,
                          secretkey=secretkey, serverkey=serverkey,
                          instance_name=self.instance_name,
                          message_bus=self.messagebus,
                          volttron_home=self.volttron_home)

        return conn

    def build_agent(self, address=None, should_spawn=True, identity=None,
                    publickey=None, secretkey=None, serverkey=None,
                    agent_class=Agent, **kwargs):
        """ Build an agent connnected to the passed bus.

        By default the current instance that this class wraps will be the
        vip address of the agent.

        :param address:
        :param should_spawn:
        :param identity:
        :param publickey:
        :param secretkey:
        :param serverkey:
        :param agent_class: Agent class to build
        :return:
        """
        self.logit("Building generic agent.")

        use_ipc = kwargs.pop('use_ipc', False)

        if serverkey is None:
            serverkey = self.serverkey
        if publickey is None:
            self.logit('generating new public secret key pair')
            keyfile = tempfile.mktemp(".keys", "agent", self.volttron_home)
            keys = KeyStore(keyfile)
            keys.generate()
            publickey = keys.public
            secretkey = keys.secret

        if address is None:
            self.logit('Using vip-address {address}'.format(
                address=self.vip_address))
            address = self.vip_address

        if publickey and not serverkey:
            self.logit('using instance serverkey: {}'.format(publickey))
            serverkey = publickey
        self.logit("BUILD agent VOLTTRON HOME: {}".format(self.volttron_home))
        agent = agent_class(address=address, identity=identity,
                            publickey=publickey, secretkey=secretkey,
                            serverkey=serverkey,
                            instance_name=self.instance_name,
                            volttron_home=self.volttron_home,
                            message_bus=self.messagebus,
                            **kwargs)
        self.logit('platformwrapper.build_agent.address: {}'.format(address))

        # Automatically add agent's credentials to auth.json file
        if publickey:
            self.logit('Adding publickey to auth.json')
            self._append_allow_curve_key(publickey)
            # gevent.spawn(self._append_allow_curve_key, publickey)
            # gevent.sleep(0.1)

        if should_spawn:
            self.logit('platformwrapper.build_agent spawning')
            event = gevent.event.Event()
            gevent.spawn(agent.core.run, event)  # .join(0)
            event.wait(timeout=2)
            gevent.sleep(1)
            hello = agent.vip.hello().get(timeout=5)

            #self.logit('Got hello response {}'.format(hello))
        agent.publickey = publickey
        return agent

    def _read_auth_file(self):
        auth_path = os.path.join(self.volttron_home, 'auth.json')
        try:
            with open(auth_path, 'r') as fd:
                data = strip_comments(FileObject(fd, close=False).read())
                if data:
                    auth = jsonapi.loads(data)
                else:
                    auth = {}
        except IOError:
            auth = {}
        if 'allow' not in auth:
            auth['allow'] = []
        return auth, auth_path

    def _append_allow_curve_key(self, publickey):
        entry = AuthEntry(credentials=publickey)
        authfile = AuthFile(self.volttron_home + "/auth.json")
        try:
            authfile.add(entry)
        except AuthFileEntryAlreadyExists:
            pass

    def add_vc(self):
        return add_volttron_central(self)

    def add_vcp(self):
        return add_volttron_central_platform(self)

    def is_auto_csr_enabled(self):
        assert self.messagebus == 'rmq', 'Only available for rmq messagebus'
        assert self.bind_web_address, 'Must have a web based instance'
        return self.dynamic_agent.vip.rpc(MASTER_WEB, 'is_auto_allow_csr').get()

    def enable_auto_csr(self):
        assert self.messagebus == 'rmq', 'Only available for rmq messagebus'
        assert self.bind_web_address, 'Must have a web based instance'
        self.dynamic_agent.vip.rpc(MASTER_WEB, 'auto_allow_csr', True).get()
        assert self.is_auto_csr_enabled()

    def disable_auto_csr(self):
        assert self.messagebus == 'rmq', 'Only available for rmq messagebus'
        assert self.bind_web_address, 'Must have a web based instance'
        self.dynamic_agent.vip.rpc(MASTER_WEB, 'auto_allow_csr', False).get()
        assert not self.is_auto_csr_enabled()

    def add_capabilities(self, publickey, capabilities):
        if isinstance(capabilities, basestring):
            capabilities = [capabilities]
        auth, auth_path = self._read_auth_file()
        cred = publickey
        allow = auth['allow']
        entry = next((item for item in allow if item['credentials'] == cred),
                     {})
        caps = entry.get('capabilities', [])
        entry['capabilities'] = list(set(caps + capabilities))

        with open(auth_path, 'w+') as fd:
            json.dump(auth, fd)

    def set_auth_dict(self, auth_dict):
        if auth_dict:
            with open(os.path.join(self.volttron_home, 'auth.json'), 'w') as fd:
                fd.write(json.dumps(auth_dict))

    def startup_platform(self, vip_address, auth_dict=None,
                         mode=UNRESTRICTED, bind_web_address=None,
                         volttron_central_address=None,
                         volttron_central_serverkey=None,
                         msgdebug=False,
                         setupmode=False,
                         agent_monitor_frequency=600,
                         timeout=60):
        self.vip_address = vip_address
        self.mode = mode
        self.volttron_central_address = volttron_central_address
        self.volttron_central_serverkey =volttron_central_serverkey

        self.bind_web_address = bind_web_address
        if self.bind_web_address:
            self.discovery_address = "{}/discovery/".format(
                self.bind_web_address)

            # Only available if vc is installed!
            self.jsonrpc_endpoint = "{}/vc/jsonrpc".format(
                self.bind_web_address)

        msgdebug = self.env.get('MSG_DEBUG', False)
        enable_logging = self.env.get('ENABLE_LOGGING', False)

        if self.debug_mode:
            self.skip_cleanup = True
            enable_logging = True
            msgdebug = True

        self.logit("Starting Platform: {}".format(self.volttron_home))
        assert self.mode in MODES, 'Invalid platform mode set: ' + str(mode)
        opts = None

        # see main.py for how we handle pub sub addresses.
        ipc = 'ipc://{}{}/run/'.format(
            '@' if sys.platform.startswith('linux') else '',
            self.volttron_home)
        self.local_vip_address = ipc + 'vip.socket'
        self.set_auth_dict(auth_dict)

        if self.messagebus == 'rmq':

            self.rabbitmq_config_obj = create_rmq_volttron_setup(vhome=self.volttron_home,
                                                                 ssl_auth=self.ssl_auth,
                                                                 env=self.env)

            self.certsobj = Certs(os.path.join(self.volttron_home, "certificates"))

            if bind_web_address:
                self.env['REQUESTS_CA_BUNDLE'] = self.certsobj.cert_file(self.certsobj.root_ca_name)

        if self.remote_platform_ca:
            ca_bundle_file = os.path.join(self.volttron_home, "cat_ca_certs")
            with open(ca_bundle_file, 'w') as cf:
                if self.messagebus == 'rmq':
                    with open(self.certsobj.cert_file(self.certsobj.root_ca_name)) as f:
                        cf.write(f.read())
                with open(self.remote_platform_ca) as f:
                    cf.write(f.read())

            self.env['REQUESTS_CA_BUNDLE'] = ca_bundle_file

        # This file will be passed off to the main.py and available when
        # the platform starts up.
        self.requests_ca_bundle = self.env.get('REQUESTS_CA_BUNDLE')

        self.opts = {'verify_agents': False,
                     'volttron_home': self.volttron_home,
                     'vip_address': vip_address,
                     'vip_local_address': ipc + 'vip.socket',
                     'publish_address': ipc + 'publish',
                     'subscribe_address': ipc + 'subscribe',
                     'bind_web_address': bind_web_address,
                     'volttron_central_address': volttron_central_address,
                     'volttron_central_serverkey': volttron_central_serverkey,
                     'platform_name': None,
                     'log': os.path.join(self.volttron_home, 'volttron.log'),
                     'log_config': None,
                     'monitor': True,
                     'autostart': True,
                     'log_level': logging.DEBUG,
                     'verboseness': logging.DEBUG,
                     'web_ca_cert': self.requests_ca_bundle}

        pconfig = os.path.join(self.volttron_home, 'config')
        config = {}

        # Add platform's public key to known hosts file
        publickey = self.keystore.public
        known_hosts_file = os.path.join(self.volttron_home, 'known_hosts')
        known_hosts = KnownHostsStore(known_hosts_file)
        known_hosts.add(self.opts['vip_local_address'], publickey)
        known_hosts.add(self.opts['vip_address'], publickey)

        # Set up the configuration file based upon the passed parameters.
        parser = configparser.ConfigParser()
        parser.add_section('volttron')
        parser.set('volttron', 'vip-address', vip_address)
        if bind_web_address:
            parser.set('volttron', 'bind-web-address', bind_web_address)
        if volttron_central_address:
            parser.set('volttron', 'volttron-central-address',
                       volttron_central_address)
        if volttron_central_serverkey:
            parser.set('volttron', 'volttron-central-serverkey',
                       volttron_central_serverkey)
        if self.instance_name:
            parser.set('volttron', 'instance-name',
                       self.instance_name)
        if self.messagebus:
            parser.set('volttron', 'message-bus',
                       self.messagebus)
        parser.set('volttron', 'agent-monitor-frequency',
                   agent_monitor_frequency)

        self.logit(
            "Platform will run on message bus type {} ".format(self.messagebus))
        self.logit("writing config to: {}".format(pconfig))

        if self.ssl_auth:
            certsdir = os.path.join(self.volttron_home, 'certificates')

            self.certsobj = Certs(certsdir)

        if self.mode == UNRESTRICTED:
            with open(pconfig, 'wb') as cfg:
                parser.write(cfg)

        elif self.mode == RESTRICTED:
            if not RESTRICTED_AVAILABLE:
                raise ValueError("restricted is not available.")

            certsdir = os.path.join(self.volttron_home, 'certificates')

            print ("certsdir", certsdir)
            self.certsobj = Certs(certsdir)

            with closing(open(pconfig, 'wb')) as cfg:
                cfg.write(PLATFORM_CONFIG_RESTRICTED.format(**config))
        else:
            raise PlatformWrapperError(
                "Invalid platform mode specified: {}".format(mode))

        log = os.path.join(self.volttron_home, 'volttron.log')

        cmd = ['volttron']
        if msgdebug:
            cmd.append('--msgdebug')
        if enable_logging:
            cmd.append('-vv')
        cmd.append('-l{}'.format(log))
        if setupmode:
            cmd.append('--setup-mode')

        from pprint import pprint
        print('process environment: ')
        pprint(self.env)
        print('popen params: {}'.format(cmd))
        self.p_process = Popen(cmd, env=self.env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)

        assert self.p_process is not None
        # A None value means that the process is still running.
        # A negative means that the process exited with an error.
        assert self.p_process.poll() is None

        # Check for VOLTTRON_PID
        sleep_time = 0
        while (not self.is_running()) and sleep_time < timeout:
            gevent.sleep(3)
            sleep_time += 3

        if sleep_time >= timeout:
            raise Exception("Platform startup failed. Please check volttron.log in {}".format(self.volttron_home))

        self.serverkey = self.keystore.public
        assert self.serverkey

        # Use dynamic_agent so we can look and see the agent with peerlist.
        self.dynamic_agent = self.build_agent(identity="dynamic_agent")
        has_control = False
        times = 0
        while not has_control and times < 10:
            times += 1
            try:
                has_control = CONTROL in self.dynamic_agent.vip.peerlist().get(timeout=.2)
                self.logit("Has control? {}".format(has_control))
            except gevent.Timeout:
                pass

        if not has_control:
            self.shutdown_platform()
            raise Exception("Couldn't connect to core platform!")

        if bind_web_address:
            times = 0
            has_discovery = False
            error_was = None

            while times < 10:
                times += 1
                try:
                    if self.ssl_auth:
                        resp = requests.get(self.discovery_address,
                                            verify=self.certsobj.cert_file(self.certsobj.root_ca_name))
                    else:
                        resp = requests.get(self.discovery_address)
                    if resp.ok:
                        self.logit("Has discovery address for {}".format(self.discovery_address))
                        if self.requests_ca_bundle:
                            self.logit("Using REQUESTS_CA_BUNDLE: {}".format(self.requests_ca_bundle))
                        else:
                            self.logit("Not using requests_ca_bundle for message bus: {}".format(self.messagebus))
                        has_discovery = True
                        break
                except Exception as e:
                    gevent.sleep(0.5)
                    error_was = e
                    self.logit("Connection error found {}".format(e))
            if not has_discovery:
                if error_was:
                    raise error_was
                raise Exception("Couldn't connect to discovery platform.")

            # Now that we know we have web and we are using ssl then we
            # can enable the WebAdminApi.
            if self.ssl_auth:
                self._web_admin_api = WebAdminApi(self)

        def subscribe_to_all(peer, sender, bus, topic, headers, messages):
            logged = "{} --------------------Pubsub Message--------------------\n".format(
                utils.format_timestamp(datetime.now()))
            logged += "PEER: {}\n".format(peer)
            logged += "SENDER: {}\n".format(sender)
            logged += "Topic: {}\n".format(topic)
            logged += "headers: {}\n".format([str(k)+'='+str(v) for k, v in headers.items()])
            logged += "message: {}\n".format(messages)
            logged += "-------------------------------------------------------\n"
            self.logit(logged)

        self.dynamic_agent.vip.pubsub.subscribe('pubsub', '', subscribe_to_all).get()

    def is_running(self):
        return utils.is_volttron_running(self.volttron_home)

    def direct_sign_agentpackage_creator(self, package):
        assert RESTRICTED, "Auth not available"
        print ("wrapper.certsobj", self.certsobj.cert_dir)
        assert (
            auth.sign_as_creator(package, 'creator',
                                 certsobj=self.certsobj)), "Signing as {} failed.".format(
            'creator')

    def direct_sign_agentpackage_admin(self, package):
        assert RESTRICTED, "Auth not available"
        assert (auth.sign_as_admin(package, 'admin',
                                   certsobj=self.certsobj)), "Signing as {} failed.".format(
            'admin')

    def direct_sign_agentpackage_initiator(self, package, config_file,
                                           contract):
        assert RESTRICTED, "Auth not available"
        files = {"config_file": config_file, "contract": contract}
        assert (auth.sign_as_initiator(package, 'initiator', files=files,
                                       certsobj=self.certsobj)), "Signing as {} failed.".format(
            'initiator')

    def _aip(self):
        opts = type('Options', (), self.opts)
        aip = AIPplatform(opts)
        aip.setup()
        return aip

    def _install_agent(self, wheel_file, start, vip_identity):
        self.logit('Creating channel for sending the agent.')
        gevent.sleep(0.3)
        self.logit('calling control install agent.')
        self.logit("VOLTTRON_HOME SETTING: {}".format(
            self.env['VOLTTRON_HOME']))
        env = self.env.copy()
        cmd = ['volttron-ctl', '-vv', 'install', wheel_file]
        if vip_identity:
            cmd.extend(['--vip-identity', vip_identity])

        res = execute_command(cmd, env=env, logger=_log)
        assert res, "failed to install wheel:{}".format(wheel_file)
        agent_uuid = res.split(' ')[-2]
        self.logit(agent_uuid)

        if start:
            self.start_agent(agent_uuid)
        return agent_uuid

        return agent_uuid

    def install_multiple_agents(self, agent_configs):
        """
        Installs mutltiple agents on the platform.

        :param agent_configs:list
            A list of 3-tuple that allows the configuration of a platform
            in a single go.  The tuple order is
            1. path to the agent directory.
            2. configuration data (either file or json data)
            3. Whether the agent should be started or not.

        :return:list:
            A list of uuid's associated with the agents that were installed.


        :Note:
            In order for this method to be called the platform must be
            currently running.
        """
        if not self.is_running():
            raise PlatformWrapperError("Instance isn't running!")
        results = []

        for path, config, start in agent_configs:
            results = self.install_agent(agent_dir=path, config_file=config,
                                         start=start)

        return results

    def install_agent(self, agent_wheel=None, agent_dir=None, config_file=None,
                      start=True, vip_identity=None, startup_time=2, force=False):
        """
        Install and optionally start an agent on the instance.

        This function allows installation from an agent wheel or an
        agent directory (NOT BOTH).  If an agent_wheel is specified then
        it is assumed to be ready for installation (has a config file).
        If an agent_dir is specified then a config_file file must be
        specified or if it is not specified then it is assumed that the
        file agent_dir/config is to be used as the configuration file.  If
        none of these exist then an assertion error will be thrown.

        This function will return with a uuid of the installed agent.

        :param agent_wheel:
        :param agent_dir:
        :param config_file:
        :param start:
        :param vip_identity:
        :param startup_time:
            How long in seconds is required for the agent to start up fully
        :param force:
            Should this overwrite the current or not.
        :return:
        """

        assert self.is_running(), "Instance must be running to install agent."
        assert agent_wheel or agent_dir, "Invalid agent_wheel or agent_dir."
        assert isinstance(startup_time, int), "Startup time should be an integer."

        if agent_wheel:
            assert not agent_dir
            assert not config_file
            assert os.path.exists(agent_wheel)
            wheel_file = agent_wheel
            agent_uuid = self._install_agent(wheel_file, start, vip_identity)

        # Now if the agent_dir is specified.
        if agent_dir:
            assert not agent_wheel
            if isinstance(config_file, dict):
                from os.path import join, basename
                temp_config = join(self.volttron_home,
                                   basename(agent_dir) + "_config_file")
                with open(temp_config, "w") as fp:
                    fp.write(json.dumps(config_file))
                config_file = temp_config
            elif not config_file:
                if os.path.exists(os.path.join(agent_dir, "config")):
                    config_file = os.path.join(agent_dir, "config")
                else:
                    from os.path import join, basename
                    temp_config = join(self.volttron_home,
                                       basename(agent_dir) + "_config_file")
                    with open(temp_config, "w") as fp:
                        fp.write(json.dumps({}))
                    config_file = temp_config
            elif os.path.exists(config_file):
                pass  # config_file already set!
            else:
                raise ValueError("Can't determine correct config file.")

            script = os.path.join(self.volttron_root,
                                  "scripts/install-agent.py")
            cmd = [self.python, script,
                   "--volttron-home", self.volttron_home,
                   "--volttron-root", self.volttron_root,
                   "--agent-source", agent_dir,
                   "--config", config_file,
                   "--json",
                   "--agent-start-time", str(startup_time)]

            if force:
                cmd.extend(["--force"])
            if vip_identity:
                cmd.extend(["--vip-identity", vip_identity])
            if start:
                cmd.extend(["--start"])

            stdout = execute_command(cmd, logger=_log,
                                     err_prefix="Error installing agent")

            self.logit(stdout)
            # Because we are no longer silencing output from the install, the
            # the results object is now much more verbose.  Our assumption is
            # that the result we are looking for is the only JSON block in
            # the output

            match = re.search(r'^({.*})', stdout, flags=re.M | re.S)
            if match:
                results = match.group(0)
            else:
                raise ValueError(
                    "The results were not found in the command output")
            self.logit("here are the results: {}".format(results))

            #
            # Response from results is expected as follows depending on
            # parameters, note this is a json string so parse to get dictionary
            # {
            #     "started": true,
            #     "agent_pid": 26241,
            #     "starting": true,
            #     "agent_uuid": "ec1fd94e-922a-491f-9878-c392b24dbe50"
            # }
            assert results

            resultobj = jsonapi.loads(str(results))

            if start:
                assert resultobj['started']
            agent_uuid = resultobj['agent_uuid']

        assert agent_uuid is not None

        if start:
            assert self.is_agent_running(agent_uuid)

        return agent_uuid

    def start_agent(self, agent_uuid):
        self.logit('Starting agent {}'.format(agent_uuid))
        self.logit("VOLTTRON_HOME SETTING: {}".format(
            self.env['VOLTTRON_HOME']))
        cmd = ['volttron-ctl']
        cmd.extend(['start', agent_uuid])
        p = Popen(cmd, env=self.env,
                  stdout=sys.stdout, stderr=sys.stderr)
        p.wait()

        # Confirm agent running
        cmd = ['volttron-ctl']
        cmd.extend(['status', agent_uuid])
        res = execute_command(cmd, env=self.env)
        # 776 TODO: Timing issue where check fails
        time.sleep(.1)
        self.logit("Subprocess res is {}".format(res))
        assert 'running' in res
        pidpos = res.index('[') + 1
        pidend = res.index(']')
        pid = int(res[pidpos: pidend])

        assert psutil.pid_exists(pid), \
            "The pid associated with agent {} does not exist".format(pid)

        self.started_agent_pids.append(pid)
        return pid

    def stop_agent(self, agent_uuid):
        # Confirm agent running
        _log.debug("STOPPING AGENT: {}".format(agent_uuid))

        cmd = ['volttron-ctl']
        cmd.extend(['stop', agent_uuid])
        res = execute_command(cmd, env=self.env, logger=_log,
                              err_prefix="Error stopping agent")
        return self.agent_pid(agent_uuid)

    def list_agents(self):
        agent_list = self.dynamic_agent.vip.rpc('control', 'list_agents').get(timeout=10)
        return agent_list

    def remove_agent(self, agent_uuid):
        """Remove the agent specified by agent_uuid"""
        _log.debug("REMOVING AGENT: {}".format(agent_uuid))

        cmd = ['volttron-ctl']
        cmd.extend(['remove', agent_uuid])
        res = execute_command(cmd, env=self.env, logger=_log,
                              err_prefix="Error removing agent")
        return self.agent_pid(agent_uuid)

    def remove_all_agents(self):
        if self._instance_shutdown:
            return
        agent_list = self.dynamic_agent.vip.rpc('control', 'list_agents').get(timeout=10)
        for agent_props in agent_list:
            self.dynamic_agent.vip.rpc('control', 'remove_agent', agent_props['uuid']).get(timeout=10)

    def is_agent_running(self, agent_uuid):
        return self.agent_pid(agent_uuid) is not None

    def agent_pid(self, agent_uuid):
        """
        Returns the pid of a running agent or None

        :param agent_uuid:
        :return:
        """
        # Confirm agent running
        cmd = ['volttron-ctl']
        cmd.extend(['status', agent_uuid])
        pid = None
        try:
            res = execute_command(cmd, env=self.env, logger=_log,
                                  err_prefix="Error getting agent status")
            try:
                pidpos = res.index('[') + 1
                pidend = res.index(']')
                pid = int(res[pidpos: pidend])
            except:
                pid = None
        except CalledProcessError as ex:
            _log.error("Exception: {}".format(ex))

        # Handle the following exception that seems to happen when getting a
        # pid of an agent during the platform shutdown phase.
        #
        # Logged from file platformwrapper.py, line 797
        #   AGENT             IDENTITY          TAG STATUS
        # Traceback (most recent call last):
        #   File "/usr/lib/python2.7/logging/__init__.py", line 882, in emit
        #     stream.write(fs % msg)
        #   File "/home/volttron/git/volttron/env/local/lib/python2.7/site-packages/_pytest/capture.py", line 244, in write
        #     self.buffer.write(obj)
        # ValueError: I/O operation on closed file
        except ValueError:
            pass
        # _log.debug("AGENT_PID: {}".format(pid))
        return pid

    def build_agentpackage(self, agent_dir, config_file={}):
        if isinstance(config_file, dict):
            cfg_path = os.path.join(agent_dir, "config_temp")
            with open(cfg_path, "w") as tmp_cfg:
                tmp_cfg.write(jsonapi.dumps(config_file))
            config_file = cfg_path

        # Handle relative paths from the volttron git directory.
        if not os.path.isabs(agent_dir):
            agent_dir = os.path.join(self.volttron_root, agent_dir)

        assert os.path.exists(config_file)
        assert os.path.exists(agent_dir)

        wheel_path = packaging.create_package(agent_dir,
                                              self.packaged_dir)
        packaging.add_files_to_package(wheel_path, {
            'config_file': os.path.join('./', config_file)
        })

        return wheel_path

    def confirm_agent_running(self, agent_name, max_retries=5,
                              timeout_seconds=2):
        running = False
        retries = 0
        while not running and retries < max_retries:
            status = self.test_aip.status_agents()
            print ("Status", status)
            if len(status) > 0:
                status_name = status[0][1]
                assert status_name == agent_name

                assert len(status[0][2]) == 2, 'Unexpected agent status message'
                status_agent_status = status[0][2][1]
                running = not isinstance(status_agent_status, int)
            retries += 1
            time.sleep(timeout_seconds)
        return running

    def restart_platform(self):
        self.startup_platform(vip_address=self.vip_address,
                              bind_web_address=self.bind_web_address,
                              volttron_central_address=self.volttron_central_address,
                              volttron_central_serverkey=self.volttron_central_serverkey)
        gevent.sleep(1)

    def stop_platform(self):
        """
        Stop the platform without cleaning up any agents or context of the
        agent.  This should be paired with restart platform in order to
        maintain the context of the platform.
        :return:
        """
        if not self.is_running():
            return

        self.dynamic_agent.vip.rpc(CONTROL, "shutdown").get()
        self.dynamic_agent.core.stop()
        if self.p_process is not None:
            try:
                gevent.sleep(0.2)
                self.p_process.terminate()
                gevent.sleep(0.2)
            except OSError:
                self.logit('Platform process was terminated.')
        else:
            self.logit("platform process was null")
        #
        # cmd = ['volttron-ctl']
        # cmd.extend(['shutdown', '--platform'])
        # try:
        #     execute_command(cmd, env=self.env, logger=_log,
        #                     err_prefix="Error shutting down platform")
        # except RuntimeError:
        #     if self.p_process is not None:
        #         try:
        #             gevent.sleep(0.2)
        #             self.p_process.terminate()
        #             gevent.sleep(0.2)
        #         except OSError:
        #             self.logit('Platform process was terminated.')
        #     else:
        #         self.logit("platform process was null")
        # gevent.sleep(1)

    def shutdown_platform(self):
        """
        Stop platform here.  First grab a list of all of the agents that are
        running on the platform, then shutdown, then if any of the listed agent
        pids are still running then kill them.
        """

        # Handle cascading calls from multiple levels of fixtures.
        if self._instance_shutdown:
            return

        if not self.is_running():
            return

        running_pids = []

        for agnt in self.list_agents():
            pid = self.agent_pid(agnt['uuid'])
            if pid is not None and int(pid) > 0:
                running_pids.append(int(pid))
        self.remove_all_agents()
        self.dynamic_agent.vip.rpc(CONTROL, 'shutdown').get()
        self.dynamic_agent.core.stop()

        if self.p_process is not None:
            try:
                gevent.sleep(0.2)
                self.p_process.terminate()
                gevent.sleep(0.2)
            except OSError:
                self.logit('Platform process was terminated.')
            pid_file = "{vhome}/VOLTTRON_PID".format(vhome=self.volttron_home)
            try:
                os.remove(pid_file)
            except OSError:
                self.logit('Error while removing VOLTTRON PID file {}'.format(pid_file))
        else:
            self.logit("platform process was null")

        for pid in running_pids:
            if psutil.pid_exists(pid):
                self.logit("TERMINATING: {}".format(pid))
                proc = psutil.Process(pid)
                proc.terminate()

        if os.environ.get('PRINT_LOG'):
            logpath = os.path.join(self.volttron_home, 'volttron.log')
            if os.path.exists(logpath):
                print("************************* Begin {}".format(logpath))
                with open(logpath) as f:
                    for l in f.readlines():
                        print(l)
                print("************************* End {}".format(logpath))
            else:
                print("######################### No Log Exists: {}".format(
                    logpath
                ))

        print(" Skip clean up flag is {}".format(self.skip_cleanup))
        if self.messagebus == 'rmq':
            print("Calling rabbit shutdown")
            stop_rabbit(rmq_home=self.rabbitmq_config_obj.rmq_home, env=self.env, quite=True)
        if not self.skip_cleanup:
            self.logit('Removing {}'.format(self.volttron_home))
            shutil.rmtree(self.volttron_home, ignore_errors=True)

        self._instance_shutdown = True

    def __repr__(self):
        return str(self)

    def __str__(self):
        data = []
        data.append('volttron_home: {}'.format(self.volttron_home))
        return '\n'.join(data)

    def cleanup(self):
        """
        Cleanup all resources created for test purpose if debug_mode is false.
        Restores orignial rabbitmq.conf if testing with rmq
        :return:
        """

        def stop_rabbit_node():
            """
            Stop RabbitMQ Server
            :param rmq_home: RabbitMQ installation path
            :param env: Environment to run the RabbitMQ command.
            :param quite:
            :return:
            """
            _log.debug("Stop RMQ: {}".format(self.volttron_home))
            cmd = [os.path.join(self.rabbitmq_config_obj.rmq_home, "sbin/rabbitmqctl"), "stop",
                   "-n", self.rabbitmq_config_obj.node_name]
            execute_command(cmd, env=self.env)
            gevent.sleep(2)
            _log.info("**Stopped rmq node: {}".format(self.rabbitmq_config_obj.node_name))

        if self.messagebus == 'rmq':
            stop_rabbit_node()

        if not self.debug_mode:
            shutil.rmtree(self.volttron_home, ignore_errors=True)


def mergetree(src, dst, symlinks=False, ignore=None):
    if not os.path.exists(dst):
        os.makedirs(dst)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            mergetree(s, d, symlinks, ignore)
        else:
            if not os.path.exists(d) or os.stat(src).st_mtime - os.stat(
                    dst).st_mtime > 1:
                shutil.copy2(s, d)


class WebAdminApi(object):
    def __init__(self, platform_wrapper=PlatformWrapper()):
        assert platform_wrapper.is_running(), "Platform must be running"
        assert platform_wrapper.bind_web_address, "Platform must have web address"
        assert platform_wrapper.ssl_auth, "Platform must be ssl enabled"

        self._wrapper = platform_wrapper
        self.bind_web_address = self._wrapper.bind_web_address
        self.certsobj = self._wrapper.certsobj

    def create_web_admin(self, username, password):
        """ Creates a global master user for the platform https interface.

        :param username:
        :param password:
        :return:
        """
        data = dict(username=username, password1=password, password2=password)
        url = self.bind_web_address +"/admin/setpassword"
        resp = requests.post(url, data, verify=self.certsobj.cert_file(self.certsobj.root_ca_name))
        assert resp.ok

        return resp
