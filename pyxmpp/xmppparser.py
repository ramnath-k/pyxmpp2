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

"""ElementTree-based XMPP stream parserg"""

from __future__ import absolute_import

__docformat__ = "restructuredtext en"

import threading
import logging

from xml.etree import ElementTree

from .exceptions import StreamParseError

COMMON_NS = "http://pyxmpp.jajcus.net/xmlns/common"

logger = logging.getLogger("pyxmpp.xmppparser")

class XMLStreamHandler(object):
    """Base class for stream handler."""
    # pylint: disable-msg=R0201
    def stream_start(self, element):
        """Called when the start tag of root element is encountered
        in the stream.

        :Parameters:
            - `element`: the root element
        :Types:
            - `element`: `ElementTree.Element`"""
        logger.error("Unhandled stream start: {0!r}".format(element))

    def stream_end(self):
        """Called when the end tag of root element is encountered
        in the stream.
        """
        logger.error("Unhandled stream end")

    def stream_element(self, element):
        """Called when the end tag of a direct child of the root
        element is encountered in the stream.

        :Parameters:
            - `element`: the (complete) element being processed
        :Types:
            - `element`: `ElementTree.Element`"""
        logger.error("Unhandled stanza: {0!r}".format(element))

    def stream_parse_error(self, descr):
        """Called when an error is encountered in the stream.

        :Parameters:
            - `descr`: description of the error
        :Types:
            - `descr`: `unicode`"""
        raise StreamParseError(descr)

class ParserTarget(object):
    """Element tree parser events handler for the XMPP stream parser."""
    def __init__(self, handler):
        """Initialize the SAX handler.

        :Parameters:
            - `handler`: Object to handle stream start, end and stanzas.
        :Types:
            - `handler`: `StreamHandler`
        """
        self._handler = handler
        self._head = ""
        self._tail = ""
        self._builder = None
        self._level = 0
        self._root = None

    def data(self, data):
        """Handle XML text data.

        Ignore the data outside the root element and directly under the root,
        pass all other text to the tree builder, so it will be included in the
        stanzas."""
        if self._level > 1:
            return self._builder.data(data)

    def start(self, tag, attrs):
        """Handle the start tag.
        
        Call the handler's 'stream_start' methods with 
        an empty root element if it is top level.
        
        For lower level tags use `ElementTree.TreeBuilder` to collect them."""
        if self._level == 0:
            self._root = ElementTree.Element(tag, attrs)
            self._handler.stream_start(self._root)
        if self._level < 2:
            self._builder = ElementTree.TreeBuilder()
        self._level += 1
        return self._builder.start(tag, attrs)

    def close(self):
        """Handle the stream end."""
        pass

    def end(self, tag):
        """Handle an end tag.
        
        Call the handler's 'stream_end' method with 
        an the root element (built by the `start` method).
        
        On the first level below root, sent the built element tree
        to the handler via the 'stanza methods'.
        
        Any tag below will be just added to the tree builder.
        """
        self._level -= 1
        if self._level < 0:
            self._handler.stream_parse_error(u"Unexpected end tag for: {0!r}"
                                                                .format(tag))
            return
        if self._level == 0:
            if tag != self._root.tag:
                self._handler.stream_parse_error(u"Unexpected end tag for:"
                            " {0!r} (stream end tag expected)".format(tag))
                return
            self._handler.stream_end()
            return
        element = self._builder.end(tag)
        if self._level == 1:
            self._handler.stream_element(element)

class StreamReader(object):
    """XML stream reader."""
    # pylint: disable-msg=R0903
    def __init__(self, handler):
        """Initialize the reader.

        :Parameters:
            - `handler`: Object to handle stream start, end and stanzas.
        :Types:
            - `handler`: `StreamHandler`
        """
        self.handler = handler
        self.parser = ElementTree.XMLParser(target = ParserTarget(handler))
        self.lock = threading.RLock()
        self.in_use = 0

    def feed(self, data):
        """Feed the parser with a chunk of data. Apropriate methods
        of `self.handler` will be called whenever something interesting is
        found.

        :Parameters:
            - `data`: the chunk of data to parse.
        :Types:
            - `data`: `str`"""
        with self.lock:
            if self.in_use:
                raise StreamParseError("StreamReader.feed() is not reentrant!")
            self.in_use = 1
            try:
                if data:
                    self.parser.feed(data)
                else:
                    self.parser.close()
            except ElementTree.ParseError, err:
                self.handler.stream_parse_error(unicode(err))
            finally:
                self.in_use = 0

# vi: sts=4 et sw=4
