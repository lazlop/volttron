import binascii
import os
from urllib.parse import urlencode

from deepdiff import DeepDiff
import jwt
import pytest

from volttron.platform.certs import CertWrapper
from volttrontesting.utils.platformwrapper import create_volttron_home
from volttrontesting.utils.web_utils import get_test_web_env, get_test_volttron_home
from volttron.platform.web.admin_endpoints import AdminEndpoints
from volttron.platform.web.authenticate_endpoint import AuthenticateEndpoints
from volttrontesting.fixtures.cert_fixtures import certs_profile_1


def __get_random_key__():
    return binascii.hexlify(os.urandom(65)).decode('utf-8')


@pytest.mark.parametrize("encryption_type", ("private_key", "tls"))
def test_jwt_encode(encryption_type):
    with get_test_volttron_home() as vhome:
        if encryption_type == "private_key":
            algorithm = "HS256"
            encoded_key = __get_random_key__()
        else:
            with certs_profile_1(vhome) as certs:
                algorithm = "RS256"
                encoded_key = CertWrapper.get_private_key(certs.server_certs[0].key_file)
        claims = {"woot": ["bah"], "all I want": 3210, "do it next": {"foo": "billy"}}
        token = jwt.encode(claims, encoded_key, algorithm)
        if encryption_type == 'tls':
            decode_key = CertWrapper.get_cert_public_key(certs.server_certs[0].cert_file)
            new_claimes = jwt.decode(token, decode_key, algorithm)
        else:
            new_claimes = jwt.decode(token, encoded_key, algorithm)

        assert not DeepDiff(claims, new_claimes)


def test_authenticate_must_use_post_request():
    with get_test_volttron_home():

        env = get_test_web_env('/authenticate')

        authorize_ep = AuthenticateEndpoints(web_secret_key=__get_random_key__())
        response = authorize_ep.get_auth_token(env, {})
        assert ('Content-Type', 'text/html') in response.headers.items()
        assert '401 Unauthorized' == response.status


def test_no_private_key_or_passphrase():
    with pytest.raises(ValueError,
                       match="Must have either ssl_private_key or web_secret_key specified!"):
        authorizeep = AuthenticateEndpoints()


def test_both_private_key_and_passphrase():
    with pytest.raises(ValueError,
                       match="Must use either ssl_private_key or web_secret_key not both!"):
        with get_test_volttron_home() as vhome:
            with certs_profile_1(vhome) as certs:
                authorizeep = AuthenticateEndpoints(web_secret_key=__get_random_key__(),
                                                    tls_private_key=certs.server_certs[0].key)


@pytest.mark.parametrize("scheme", ("http", "https"))
def test_authenticate_endpoint(scheme):
    vhome = create_volttron_home()
    kwargs = {}

    # Note this is not a context wrapper, it just does the creation for us
    vhome = create_volttron_home()

    if scheme == 'https':
        with certs_profile_1(vhome) as certs:
            kwargs['web_ssl_key'] = certs.server_certs[0].key_file
            kwargs['web_ssl_cert'] = certs.server_certs[0].cert_file
    else:
        kwargs['web_secret_key'] = __get_random_key__()

    # We are specifying the volttron_home here so we don't create an additional one.
    with get_test_volttron_home(volttron_config_params=kwargs, volttron_home=vhome):

        user = 'bogart'
        passwd = 'cat'
        adminep = AdminEndpoints()
        adminep.add_user(user, passwd)

        env = get_test_web_env('/authenticate', method='POST')

        if scheme == 'http':
            authorizeep = AuthenticateEndpoints(web_secret_key=kwargs.get('web_secret_key'))
        else:
            authorizeep = AuthenticateEndpoints(tls_private_key=CertWrapper.load_key(kwargs.get('web_ssl_key')))

        invalid_login_username_params = dict(username='fooey', password=passwd)

        response = authorizeep.get_auth_token(env, invalid_login_username_params)

        assert '401' == response.status
        # TODO: Get the actual response content here
        # assert '401 Unauthorized' in response.content

        invalid_login_password_params = dict(username=user, password='hazzah')
        response = authorizeep.get_auth_token(env, invalid_login_password_params)

        assert '401' == response.status
        valid_login_params = urlencode(dict(username=user, password=passwd))
        response = authorizeep.get_auth_token(env, valid_login_params)
        assert '200 OK' == response.status
        assert "text/plain" in response.content_type
        assert 3 == len(response.response[0].decode('utf-8').split('.'))

# def test_authenticate_tls():
#     user = 'fred'
#     passwd = 'sam'
#     with get_test_volttron_home() as vhome:
#         # Store a real username/password
#         adminep = AdminEndpoints()
#         adminep.add_user(user, passwd)
#
#         certs_dir = os.path.join(vhome, "certs")
#         with certs_profile_1(certs_dir) as mycerts:
#             server_cert = mycerts.server_certs[0]
#
#             # build a custom env to pass to the end point
#             env = get_test_web_env('/authenticate', method='POST')
#
#             authorizeep = AuthenticateEndpoints(ssl_private_key=server_cert.key)
#             invalid_login_username_params = urlencode(dict(username='fooey', password=passwd))
#             response = authorizeep.get_auth_token(env, invalid_login_username_params.encode('utf-8'))
#
#             assert '401' == response.status
#
#             assert_valid_authorization(authorizeep, env, user, passwd)
