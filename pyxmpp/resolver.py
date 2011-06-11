#
# (C) Copyright 2003-2011 Jacek Konieczny <jajcus@jajcus.net>
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

"""DNS resolever with SRV record support.

Normative reference:
  - `RFC 1035 <http://www.ietf.org/rfc/rfc1035.txt>`__
  - `RFC 2782 <http://www.ietf.org/rfc/rfc2782.txt>`__
"""

from __future__ import absolute_import

__docformat__ = "restructuredtext en"

import socket
import random
import logging

from abc import ABCMeta

from .settings import XMPPSettings

logger = logging.getLogger("pyxmpp.resolver")

try:
    import dns.resolver
    import dns.name
    import dns.exception

    HAVE_DNSPYTHON = True
except ImportError:
    HAVE_DNSPYTHON = False

class Resolver:
    """Abstract base class for asynchronous DNS resolvers to be used
    with PyxMPP.
    """
    # pylint: disable-msg=W0232
    __metaclass__ = ABCMeta
    def resolve_srv(self, domain, service, protocol, callback):
        """Start looking up an SRV record for `service` at `address`.

        `callback` will be called with a properly sorted list of (hostname,
        port) pairs on success. The list will be empty on error and it will
        contain only (".", 0) when the service is explicitely disabled.

        :Parameters:
            - `domain`: domain name to look up
            - `service`: service name e.g. 'xmpp-client'
            - `protocol`: protocol name, e.g. 'tcp'
            - `callback`: a function to be called with a list of received
              addresses
        :Types:
            - `domain`: `unicode`
            - `service`: `unicode`
            - `protocol`: `unicode`
            - `callback`: function accepting a single argument
        """
        raise NotImplementedError

    def resolve_address(self, hostname, callback, allow_cname = True):
        """Start looking up an A or AAAA record.

        `callback` will be called with a list of IPv4 or IPv6 address literals
        on success. The list will be empty on error.

        :Parameters:
            - `hostname`: the host name to look up
            - `callback`: a function to be called with a list of received
              addresses
            - `allow_cname`: `True` if CNAMEs should be followed
        :Types:
            - `hostname`: `unicode`
            - `callback`: function accepting a single argument
            - `allow_cname`: `bool`
        """
        raise NotImplementedError

def is_ipv6_available():
    """Check if IPv6 is available.
    
    :Return: `True` when an IPv6 socket can be created.
    """
    try:
        socket.socket(socket.AF_INET6)
    except (socket.error, AttributeError):
        return False
    return True

def is_ipv4_available():
    """Check if IPv4 is available.
    
    :Return: `True` when an IPv4 socket can be created.
    """
    try:
        socket.socket(socket.AF_INET)
    except socket.error:
        return False
    return True

def shuffle_srv(records):
    """Randomly reorder SRV records using their weights.

    :Parameters:
        - `records`: SRV records to shuffle.
    :Types:
        - `records`: sequence of `dns.rdtypes.IN.SRV`

    :return: reordered records.
    :returntype: `list` of `dns.rdtypes.IN.SRV`"""
    if not records:
        return []
    ret = []
    while len(records) > 1:
        weight_sum = 0
        for rrecord in records:
            weight_sum += rrecord.weight + 0.1
        thres = random.random() * weight_sum
        weight_sum = 0
        for rrecord in records:
            weight_sum += rrecord.weight + 0.1
            if thres < weight_sum:
                records.remove(rrecord)
                ret.append(rrecord)
                break
    ret.append(records[0])
    return ret

def reorder_srv(records):
    """Reorder SRV records using their priorities and weights.

    :Parameters:
        - `records`: SRV records to shuffle.
    :Types:
        - `records`: `list` of `dns.rdtypes.IN.SRV`

    :return: reordered records.
    :returntype: `list` of `dns.rdtypes.IN.SRV`"""
    records = list(records)
    records.sort()
    ret = []
    tmp = []
    for rrecord in records:
        if not tmp or rrecord.priority == tmp[0].priority:
            tmp.append(rrecord)
            continue
        ret += shuffle_srv(tmp)
        tmp = [rrecord]
    if tmp:
        ret += shuffle_srv(tmp)
    return ret

if HAVE_DNSPYTHON:
    class BlockingResolver(Resolver):
        """Blocking resolver using the DNSPython package.

        Both `resolve_srv` and `resolve_hostname` will block until the 
        lookup completes or fail and then call the callback immediately.
        """
        def __init__(self, settings =  None):
            if settings:
                self.settings = settings
            else:
                self.settings = XMPPSettings()
            
        def resolve_srv(self, domain, service, protocol, callback):
            """Start looking up an SRV record for `service` at `address`.

            `callback` will be called with a properly sorted list of (hostname,
            port) pairs on success. The list will be empty on error and it will
            contain only (".", 0) when the service is explicitely disabled.

            :Parameters:
                - `domain`: domain name to look up
                - `service`: service name e.g. 'xmpp-client'
                - `protocol`: protocol name, e.g. 'tcp'
                - `callback`: a function to be called with a list of received
                  addresses
            :Types:
                - `domain`: `unicode`
                - `service`: `unicode`
                - `protocol`: `unicode`
                - `callback`: function accepting a single argument
            """
            if isinstance(domain, unicode):
                domain = domain.encode("idna")
            try:
                records = dns.resolver.query(domain, 'SRV')
            except dns.exception.DNSException, err:
                logger.warning("Could not resolve {0!r}: {1}", domain, 
                                                        err.__class__.__name__)
                callback([])
                return
            if not records:
                callback([])
                return

            result = []
            for record in reorder_srv(records):
                hostname = record.target.to_text()
                if hostname in (".", ""):
                    continue
                result.append(hostname, record.port)

            if not result:
                callback([(".", 0)])
            else:
                callback(result)
            return

        def resolve_address(self, hostname, callback, allow_cname = True):
            """Start looking up an A or AAAA record.

            `callback` will be called with a list of (family, address) tuples
            (each holiding socket.AF_*  and IPv4 or IPv6 address literal) on
            success. The list will be empty on error.

            :Parameters:
                - `hostname`: the host name to look up
                - `callback`: a function to be called with a list of received
                  addresses
                - `allow_cname`: `True` if CNAMEs should be followed
            :Types:
                - `hostname`: `unicode`
                - `callback`: function accepting a single argument
                - `allow_cname`: `bool`
            """
            if isinstance(hostname, unicode):
                hostname = hostname.encode("idna")
            rtypes = []
            if self.settings["allow_ipv6"]:
                rtypes.append(("AAAA", socket.AF_INET6))
            if self.settings["allow_ipv4"]:
                rtypes.append(("A", socket.AF_INET))
            exception = None
            result = []
            for rtype, rfamily in rtypes:
                try:
                    try:
                        records = dns.resolver.query(hostname, rtype)
                    except dns.exception.DNSException:
                        records = dns.resolver.query(hostname + ".", rtype)
                except dns.exception.DNSException, err:
                    exception = err
                    continue
                if not allow_cname and records.rrset.name != dns.name.from_text(
                                                                    hostname):
                    logger.warning("Unexpected CNAME record found for {0!r}"
                                                            .format(hostname))
                    continue
                if records:
                    for record in records:
                        result.append((rfamily, record.to_text()))

            if not result and exception:
                logger.warning("Could not resolve {0!r}: {1}".format(hostname,
                                                exception.__class__.__name__))
            callback(result)

class DummyBlockingResolver(Resolver):
    """Simple blocking resolver using only the standard Python library.
    
    This doesn't support SRV lookups!

    `resolve_srv` will raise NotImplementedError
    `resolve_hostname` will block until the lookup completes or fail and then
    call the callback immediately.
    """
    # pylint: disable-msg=R0921
    def __init__(self, settings):
        if settings:
            self.settings = settings
        else:
            self.settings = XMPPSettings()

    def resolve_srv(self, domain, service, protocol, callback):
        """Start looking up an SRV record for `service` at `address`.

        `callback` will be called with a properly sorted list of (hostname,
        port) pairs on success. The list will be empty on error and it will
        contain only (".", 0) when the service is explicitely disabled.

        :Parameters:
            - `domain`: domain name to look up
            - `service`: service name e.g. 'xmpp-client'
            - `protocol`: protocol name, e.g. 'tcp'
            - `callback`: a function to be called with a list of received
              addresses
        :Types:
            - `domain`: `unicode`
            - `service`: `unicode`
            - `protocol`: `unicode`
            - `callback`: function accepting a single argument
        """
        raise NotImplementedError("The DummyBlockingResolver cannot resolve"
                " SRV records. DNSPython or target hostname explicitely set"
                                                                " required")

    def resolve_address(self, hostname, callback, allow_cname = True):
        """Start looking up an A or AAAA record.

        `callback` will be called with a list of IPv4 or IPv6 address literals
        on success. The list will be empty on error.

        :Parameters:
            - `hostname`: the host name to look up
            - `callback`: a function to be called with a list of received
              addresses
            - `allow_cname`: `True` if CNAMEs should be followed
        :Types:
            - `hostname`: `unicode`
            - `callback`: function accepting a single argument
            - `allow_cname`: `bool`
        """
        if self.settings["allow_ipv6"]:
            if self.settings["allow_ipv4"]:
                family = socket.AF_UNSPEC
            else:
                family = socket.AF_INET6
        elif self.settings["allow_ipv4"]:
            family = socket.AF_INET
        else:
            logger.warning("Neither IPv6 or IPv4 allowed.")
            callback([])
            return
        try:
            ret = socket.getaddrinfo(hostname, 0, family, socket.SOCK_STREAM, 0)
        except socket.gaierror, err:
            logger.warning("Couldn't resolve {0!r}: {1}".format(hostname,
                                                                        err))
            callback([])
            return
        if family == socket.AF_UNSPEC:
            if self.settings["prefer_ipv6"]:
                ret = [ addr for addr in ret if addr[0] == socket.AF_INET6 ]
                ret += [ addr for addr in ret if addr[0] == socket.AF_INET ]
            else:
                ret = [ addr for addr in ret if addr[0] == socket.AF_INET ]
                ret += [ addr for addr in ret if addr[0] == socket.AF_INET6 ]
        callback([(addr[0], addr[4][0]) for addr in ret])

XMPPSettings.add_defaults({
            u"allow_ipv4": True,
            u"prefer_ipv6": True,
            })

XMPPSettings.add_default_factory("dns_resolver", BlockingResolver)
XMPPSettings.add_default_factory("allow_ipv6", lambda x: is_ipv6_available(),
                                                                        True)
# vi: sts=4 et sw=4
