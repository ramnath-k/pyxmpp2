#
# (C) Copyright 2011 Jacek Konieczny <jajcus@jajcus.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License Version
# 2.1 as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
#
"""SCRAM authentication mechanisms for PyXMPP SASL implementation.

Normative reference:
  - :RFC:`5802`
"""

from __future__ import absolute_import, division

__docformat__ = "restructuredtext en"

import sys
import re
import logging
import hashlib
import hmac
import ssl

from binascii import a2b_base64
from base64 import standard_b64encode

from .core import ClientAuthenticator, ServerAuthenticator
from .core import Failure, Response, Challenge, Success, Failure
from .core import sasl_mechanism, default_nonce_factory
from .saslprep import SASLPREP
        
logger = logging.getLogger("pyxmpp2.sasl.scram")

HASH_FACTORIES = {
        "SHA-1": hashlib.sha1,      # pylint: disable=E1101
        "SHA-224": hashlib.sha224,  # pylint: disable=E1101
        "SHA-256": hashlib.sha256,  # pylint: disable=E1101
        "SHA-384": hashlib.sha384,  # pylint: disable=E1101
        "SHA-512": hashlib.sha512,  # pylint: disable=E1101
        "MD-5": hashlib.md5,        # pylint: disable=E1101
        }

VALUE_CHARS_RE = re.compile(br"^[\x21-\x2B\x2D-\x7E]+$")
SERVER_FIRST_MESSAGE_RE = re.compile(
                                br"^(?P<mext>m=[^\000=]+,)?"
                                br"(?:r=(?P<nonce>[\x21-\x2B\x2D-\x7E]+)),"
                                br"(?:s=(?P<salt>[a-zA-Z0-9/+=]+)),"
                                br"(?:i=(?P<iteration_count>\d+))"
                                br"(?:,.*)?$"
                                )
SERVER_FINAL_MESSAGE_RE = re.compile(
        br"^(?:e=(?P<error>[^,]+)|v=(?P<verifier>[a-zA-Z0-9/+=]+)(?:,.*)?)$")

class SCRAMOperations(object):
    """Functions used during SCRAM authentication and defined in the RFC.

    """
    def __init__(self, hash_function_name):
        self.hash_function_name = hash_function_name
        self.hash_factory = HASH_FACTORIES[hash_function_name]
        self.digest_size = self.hash_factory().digest_size

    @staticmethod
    def Normalize(str_):
        """The Normalize(str) function.

        This one also accepts Unicode string input (in the RFC only UTF-8
        strings are used).
        """
        # pylint: disable=C0103
        if isinstance(str_, bytes):
            str_ = str_.decode("utf-8")
        return SASLPREP.prepare(str_).encode("utf-8")

    def HMAC(self, key, str_):
        """The HMAC(key, str) function."""
        # pylint: disable=C0103
        return hmac.new(key, str_, self.hash_factory).digest()

    def H(self, str_):
        """The H(str) function."""
        # pylint: disable=C0103
        return self.hash_factory(str_).digest()

    if sys.version_info.major >= 3:
        @staticmethod
        # pylint: disable=C0103
        def XOR(str1, str2):
            """The XOR operator for two byte strings."""
            return bytes(a ^ b for a, b in zip(str1, str2))
    else:
        @staticmethod
        # pylint: disable=C0103
        def XOR(str1, str2):
            """The XOR operator for two byte strings."""
            return "".join(chr(ord(a) ^ ord(b)) for a, b in zip(str1, str2))

    def Hi(self, str_, salt, i):
        """The Hi(str, salt, i) function."""
        # pylint: disable=C0103
        Uj = self.HMAC(str_, salt + b"\000\000\000\001") # U1
        result = Uj
        for _ in range(2, i + 1):
            Uj = self.HMAC(str_, Uj)               # Uj = HMAC(str, Uj-1)
            result = self.XOR(result,  Uj)         # ... XOR Uj-1 XOR Uj
        return result

class SCRAMClientAuthenticator(SCRAMOperations, ClientAuthenticator):
    """Provides SCRAM SASL authentication for a client.

    :Ivariables:
        - `password`: current authentication password
        - `pformat`: current authentication password format
        - `realm`: current authentication realm
    """
    # pylint: disable-msg=R0902
    def __init__(self, hash_name, channel_binding):
        """Initialize a `SCRAMClientAuthenticator` object.

        :Parameters:
            - `hash_function_name`: hash function name, e.g. ``"SHA-1"``
            - `channel_binding`: `True` to enable channel binding
        :Types:
            - `hash_function_name`: `unicode`
            - `channel_binding`: `bool`
        """
        ClientAuthenticator.__init__(self)
        SCRAMOperations.__init__(self, hash_name)
        self.name = "SCRAM-{0}".format(hash_name)
        if channel_binding:
            self.name += "-PLUS"
        self.channel_binding = channel_binding
        self.username = None
        self.password = None
        self.authzid = None
        self._c_nonce = None
        self._server_first_message = False
        self._client_first_message_bare = False
        self._gs2_header = None
        self._finished = False
        self._auth_message = None
        self._salted_password = None
        self._cb_data = None

    @classmethod
    def are_properties_sufficient(cls, properties):
        return "username" in properties and "password" in properties

    def start(self, properties):
        self.username = properties["username"]
        self.password = properties["password"]
        self.authzid = properties.get("authzid", u"")
        c_nonce = properties.get("nonce_factory", default_nonce_factory)()
        if not VALUE_CHARS_RE.match(c_nonce):
            c_nonce = standard_b64encode(c_nonce)
        self._c_nonce = c_nonce

        if self.channel_binding:
            cb_data = properties.get("channel-binding")
            if not cb_data:
                raise ValueError("No channel binding data provided")
            if "tls-unique" in cb_data:
                cb_type = "tls-unique"
            elif "tls-server-end-point" in cb_data:
                cb_type = "tls-server-end-point"
            elif cb_data:
                cb_type = cb_data.keys()[0]
            self._cb_data = cb_data[cb_type]
            cb_flag = b"p=" + cb_type.encode("utf-8")
        else:
            plus_name = self.name + "-PLUS"
            if plus_name in properties.get("enabled_mechanisms", []):
                cb_flag = b"y"
            else:
                cb_flag = b"n"

        if self.authzid:
            authzid = b"a=" + self.authzid.encode("utf-8")
        else:
            authzid = b""
        gs2_header = cb_flag + b"," + authzid + b","
        self._gs2_header = gs2_header
        nonce = b"r=" + c_nonce 
        client_first_message_bare = (b"n=" + self.username.encode("utf-8") + 
                                                        b"," + nonce)
        self._client_first_message_bare = client_first_message_bare
        client_first_message = gs2_header + client_first_message_bare
        return Response(client_first_message)

    def challenge(self, challenge):
        """Process a challenge and return the response.

        :Parameters:
            - `challenge`: the challenge from server.
        :Types:
            - `challenge`: `bytes`

        :return: the response or a failure indicator.
        :returntype: `sasl.Response` or `sasl.Failure`
        """
        # pylint: disable=R0911
        if not challenge:
            logger.debug("Empty challenge")
            return Failure("bad-challenge")

        if self._server_first_message:
            return self._final_challenge(challenge)

        match = SERVER_FIRST_MESSAGE_RE.match(challenge)
        if not match:
            logger.debug("Bad challenge syntax: {0!r}".format(challenge))
            return Failure("bad-challenge")

        self._server_first_message = challenge

        mext = match.group("mext")
        if mext:
            logger.debug("Unsupported extension received: {0!r}".format(mext))
            return Failure("bad-challenge")

        nonce = match.group("nonce")
        if not nonce.startswith(self._c_nonce):
            logger.debug("Nonce does not start with our nonce")
            return Failure("bad-challenge")

        salt = match.group("salt")
        try:
            salt = a2b_base64(salt)
        except ValueError:
            logger.debug("Bad base64 encoding for salt: {0!r}".format(salt))
            return Failure("bad-challenge")

        iteration_count = match.group("iteration_count")
        try:
            iteration_count = int(iteration_count)
        except ValueError:
            logger.debug("Bad iteration_count: {0!r}".format(iteration_count))
            return Failure("bad-challenge")

        return self._make_response(nonce, salt, iteration_count)

    def _make_response(self, nonce, salt, iteration_count):
        """Make a response for the first challenge from the server.

        :return: the response or a failure indicator.
        :returntype: `sasl.Response` or `sasl.Failure`
        """
        self._salted_password = self.Hi(self.Normalize(self.password), salt,
                                                            iteration_count)
        self.password = None # not needed any more
        if self.channel_binding:
            channel_binding = b"c=" + standard_b64encode(self._gs2_header +
                                                                self._cb_data)
        else:
            channel_binding = b"c=" + standard_b64encode(self._gs2_header)

        # pylint: disable=C0103
        client_final_message_without_proof = (channel_binding + b",r=" + nonce)
        
        client_key = self.HMAC(self._salted_password, b"Client Key")
        stored_key = self.H(client_key)
        auth_message = ( self._client_first_message_bare + b"," +
                                    self._server_first_message + b"," +
                                        client_final_message_without_proof )
        self._auth_message = auth_message
        client_signature = self.HMAC(stored_key, auth_message)
        client_proof = self.XOR(client_key, client_signature)
        proof = b"p=" + standard_b64encode(client_proof)
        client_final_message = (client_final_message_without_proof + b"," +
                                                                    proof)
        return Response(client_final_message)

    def _final_challenge(self, challenge):
        """Process the second challenge from the server and return the
        response.

        :Parameters:
            - `challenge`: the challenge from server.
        :Types:
            - `challenge`: `bytes`

        :return: the response or a failure indicator.
        :returntype: `sasl.Response` or `sasl.Failure`
        """
        if self._finished:
            return Failure("extra-challenge")

        match = SERVER_FINAL_MESSAGE_RE.match(challenge)
        if not match:
            logger.debug("Bad final message syntax: {0!r}".format(challenge))
            return Failure("bad-challenge")

        error = match.group("error")
        if error:
            logger.debug("Server returned SCRAM error: {0!r}".format(error))
            return Failure(u"scram-" + error.decode("utf-8"))

        verifier = match.group("verifier")
        if not verifier:
            logger.debug("No verifier value in the final message")
            return Failure("bad-succes")
        
        server_key = self.HMAC(self._salted_password, b"Server Key")
        server_signature = self.HMAC(server_key, self._auth_message)
        if server_signature != a2b_base64(verifier):
            logger.debug("Server verifier does not match")
            return Failure("bad-succes")

        self._finished = True
        return Response(None)

    def finish(self, data):
        """Process success indicator from the server.

        Process any addiitional data passed with the success.
        Fail if the server was not authenticated.

        :Parameters:
            - `data`: an optional additional data with success.
        :Types:
            - `data`: `bytes`

        :return: success or failure indicator.
        :returntype: `sasl.Success` or `sasl.Failure`"""
        if not self._server_first_message:
            logger.debug("Got success too early")
            return Failure("bad-success")
        if self._finished:
            return Success({"username": self.username, "authzid": self.authzid})
        else:
            ret = self._final_challenge(data)
            if isinstance(ret, Failure):
                return ret
            if self._finished:
                return Success({"username": self.username,
                                                    "authzid": self.authzid})
            else:
                logger.debug("Something went wrong when processing additional"
                                                        " data with success?")
                return Failure("bad-success")

@sasl_mechanism("SCRAM-SHA-1", 80)
class SCRAM_SHA_1_ClientAuthenticator(SCRAMClientAuthenticator):
    """The SCRAM-SHA-1 client authenticator.
    """
    # pylint: disable=C0103
    def __init__(self):
        SCRAMClientAuthenticator.__init__(self, "SHA-1", False)

if getattr(ssl, "HAS_TLS_UNIQUE", None):
    @sasl_mechanism("SCRAM-SHA-1-PLUS", 90)
    class SCRAM_SHA_1_PLUS_ClientAuthenticator(SCRAMClientAuthenticator):
        """The SCRAM-SHA-1-PLUS client authenticator.
        """
        # pylint: disable=C0103
        def __init__(self):
            SCRAMClientAuthenticator.__init__(self, "SHA-1", True)
        @classmethod
        def are_properties_sufficient(cls, properties):
            ret = super(SCRAM_SHA_1_PLUS_ClientAuthenticator, cls
                                    ).are_properties_sufficient(properties)
            if not ret:
                return False
            return bool(properties.get("channel-binding"))

