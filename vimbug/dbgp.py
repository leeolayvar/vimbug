# -*- coding: utf-8 -*-
'''
    vimbug.dbgp
    ~~~~~~~~~~~

    A collection of classes to handle protocol communications with a
    DBGp server.

    :copyright: (c) 2011 by Lee Olayvar.
    :license: MIT, see LICENSE for more details.
'''

import base64
import socket, select
import subprocess
import logging

from lxml import etree


logger = logging.getLogger('vimbug.dbgp')


class DBGP:
    '''A friendly frontend which allows for cleaner access to a DBGp Server.

    Communication itself is handled by :class:`DBGPConnection`.
    '''
    pass

class DBGPConnection:
    '''
    '''


    def __init__(self, starter, hostname='localhost', port=9000):
        '''
        :param starter:
            When a debug session is needed, this object is called *(as a
            function call)*.
        :param hostname:
            The hostname to use for this connection.
        :param port:
            The port to use for this connection.
        '''
        #: The hostname which will be listening.
        self._hostname = hostname
        #: The port which will be listening on.
        self._port = port
        #: The object called when the IDE is listening for a connection from a
        #: DBGp Server.
        self._starter = starter

        
        #: A listener for incoming DBGp Server connections.
        self._listener = SocketListener()
   
    def connect(self):
       '''Start listening for an ide connection, and call this connections
       starter object, if any.
       '''
       # Start listening for connections.
       self._listener.listen(hostname=self._hostname, port=self._port)
       # Call the starter.
       self._starter()
       # Accept any connections
       self._listener.accept()

       self._connected = self._listener.connected()

    def connected(self):
       '''Check whether or not a connection is active with this DBGPConnection
       '''
       return self._connected

    def disconnect(self, stop=True):
        '''Close the DBGp Socket Connection.
        
        :param stop:
            Send the stop command to the DBGp Server before disconnecting the
            connection.
        '''
        if stop:
            self.send('stop')

        self._listener.close()
        self._connected = False

    def receive(self):
        '''Receive whatever data is in queue and convert it to an etree XML
        object.

        :returns:
            An `lxml.etree.Element` object, or `None` if no data is received.
        '''
        data = self.receive_string()
        if data is None:
            return etree.fromstring(data)
        else:
            return None

    def receive_string(self):
        '''Receive whatever data is in queue and return it.

        :returns:
            Any data in the queue.
        '''
        return self._listener.socket.receive()

    def send(self, command, data=None, **kwargs):
        '''Send a command to the DBGp Server.

        :param command:
            The command to send to the DBGp Server.
        :param data:
            Any additional data to pass with the command. An example of this
            would be code for an expression.
        :param **kwargs:
            All additional keyword arguments will be appended to the
            command string in the format of '-key value'.
        '''
        # Start by assigning the command to the command string.
        command_string = command
        # Now append each item to the command string.
        for key, value in kwargs.items():
            command_string = '%(orig_str)s -%(key)s %(value)s' % {
                'orig_str':command_string,
                'key':key,
                'value':value,
            }
        # And if there is any data, add that to the command string in base64
        # format.
        if data is not None:
            # Note that we are removing the last character here as it
            # is a return character for some reason. We don't want this.
            encoded_data = base64.encodestring(data)[:-1]
            command_string = '%(orig_str)s -l %(data_len)s -- %(data)s' % {
                'data_len':len(encoded_data),
                'orig_str':command_string,
                'data':encoded_data,
            }

        # Add our ending Null!
        command_string += '\0'

        # Lastly, log our send and send it!
        logger.debug('DBGPConnection Send: %s' % command_string)
        print 'Sending.. %s' % command_string
        self._listener.socket.send(command_string)


class DBGPServerNotFoundError(Exception):
    '''The DBGp Server did not connect to a listening client.'''
    pass


class PyDBGPStarter(object):
    '''When an instance is called, initialize a pydbgp server.'''


    def __init__(self, url, file_args=None, hostname='localhost', port=9000):
        '''
        :param url:
            The url of the python file.
        :param file_args:
            Arguments passed to the file. None by default.
        :param hostname:
            The hostname to use for this connection.
        :param port:
            The port to use for this connection.
        '''
        self.url = url
        if file_args is None:
            file_args = tuple()
        self.file_args = file_args
        self.hostname = hostname
        self.port = port

    def __call__(self):
        '''Start a pydbgp.py subprocess.'''
        address = '%s:%s' % (self.hostname, self.port)
        self._pydbgp_proc = subprocess.Popen(
            ('pydbgp.py', '-d', address, self.url) + self.file_args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

class Socket(object):
    '''A simple socket wrapper designed to make dealing with sockets cleaner,
    **in this context**.
    '''
    

    def __init__(self, socket_=None):
        '''
        :param socket_:
            An instance of a `socket.socket()` like object.
        '''
        if socket_ is None:
            socket_ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        #: True if a connection has been made. False otherwise.
        self._connected = False
        #: An instance of a `socket.socket()` like object.
        self._socket = socket_

    def _receive(self, length):
        '''Receive a set number of characters from the socket.

        :param length:
            The length of the data to read.

        :raises EOFError:
            Raised if the socket receives no more data.
        '''
        # We will store our data by appending each recv result to this.
        data = ''

        while length > 0:
            # While we still want to read data.

            # Get the sockets recv.
            buffer = self._socket.recv(length)

            if buffer == '':
                # If we receive nothing, the connection has closed on the
                # other end.

                self.close()
                raise EOFError('The client has closed the connection.')

            # Append whatever we received to the total data.
            data += buffer
            # Ensure we read as much as we intended to read by subtracting
            # what we *actually* read from the original intention.
            length -= len(buffer)

        if data:
            return data
        else:
            return None

    def _receive_length(self):
        '''Read the length of the socket buffer by getting a sequence of
        integers found at the beginning of the buffer.

        :raises EOFError:
            Raised if the sock receives no more data.
        :raises Exception:
            Raised if an unexpected result was returned from the server.
        '''

        # The characters found thus far.
        chars = ''

        while True:
            # Now, get a char from the socket.

            # Note that this whole section badly needs a rewrite..
            # It's just.. ugly. That's what i get for modifying code rather
            # than writing from scratch.
            # -- Mark Twain
            reads, writes, errs = select.select([self._socket], [], [], 0)

            if self._socket in reads:
                c = self._socket.recv(1)
            else:
                return 0

            if c == '':
                # If c is empty, the connection has been closed. So we need
                # to shut down, and signal the end of the connection.
                self.close()
                raise EOFError('The server has closed the connection.')
            elif c == '\0':
                # If \0 is returned we have reached the end of the length
                # characters. So return what we have gathered thus far in.
                if chars == '':
                    return 0
                length = int(chars)
                # Don't forget to break the loop!
                break
            elif c.isdigit():
                # If c is a digit, we want to append it to chars and repeat
                # this wheel of fun.
                chars += c
                # Restart the loop
                continue
            else:
                # If we reach here, C is not empty, not \0, and not a digit.
                # What is it!? Well, lets fail it since something obviously
                # isn't right.
                raise Exception(
                    'An unexpected result of "%s" was received from the '
                    'client socket.' % c)
        
        # Not much else to do at this point. Return our length! If length
        # doesn't exist here, we have a bug, so let's not worry about it.
        return length

    def close(self):
        '''Close the socket connection.'''
        self._socket.close()
        self._connected = False

    def connect(self, hostname='localhost', port=9000):
        '''Connect to a socket at the given address.

        :param hostname:
            The hostname to connect to.
        :param port:
            The port to connect to the host on.
        '''
        try:
            self._socket.connect((hostname, port))
        except socket.error, error:
            # We're just letting any errors bubble up from this. No reason
            # currently to try and catch them all.
            raise error
        else:
            self._connected = True

    def connected(self):
        '''Check whether or not this socket is connected. Note that this is
        mostly just checking if the connection has ever been connected. The
        connection on the other end may have died, and you won't know until
        a read fails.

        :returns:
            True if connected. False otherwise.
        '''
        return self._connected

    def receive(self):
        '''Read from the socket connection.'''
        return self._receive(self._receive_length())

    def send(self, data, prefix_length=False, prefix_separator='\0'):
        '''Send data to the server.

        :param data:
            The data to send.
        :param prefix_length:
            If True, this will prefix the data with the length of the data,
            followed by the prefix chatacter.
        :param prefix_separator:
            If prefix_length is True, this string will be placed between the
            length and the data.
        '''
        if prefix_length:
            data = ''.join((len(data), prefix_separator, data))

        self._socket.send(data)


class SocketConnectionFailedError(Exception):
    '''Raised if a socket was unable to connect.'''
    pass


class SocketListener(object):
    '''A simple socket wrapper designed to make listening and accepting
    connections cleaner **in this context**.
    '''

    
    def __init__(self):
        ''''''
        #: The listening socket.
        self._listening_socket = None
        #: The data socket
        self.socket = None

    def __enter__(self):
        '''Setup code for this connection object.'''
        # I don't think anything needs to be done here. Calling
        # self._listening_socket.__enter__() may be a possibility though.
        pass

    def __exit__(self):
        '''Call close on this connection.'''
        self.close()

    def accept(self):
        '''Accept a connection, if one has been made.

        :returns:
            A socket connection that was made. None, if no socket connections
            were established.
        '''

        inputs, outputs, exceptions = select.select(
            [self._listening_socket], [], [], 1)

        if self._listening_socket in inputs:
            (client_socket,
             client_address) = self._listening_socket.accept()
   
            self.socket = Socket(client_socket)
            # Here we need to make sure and tell the wrapper that it is connected.
            self.socket._connected = True

            (self._client_hostname, self._client_port) = client_address

            logger.debug('SocketListener connection established! The other '
                         'end of the connection is '
                         'at "%s:%i".' % client_address)
        else:
            logger.debug('SocketListener had no connections made.')

        # Now we need to close *only* the listener. Since this could have been
        # a successful connection.
        self._listening_socket.close()

    def close(self):
        '''Close the socket connection.'''

        logger.debug('Closing socket listener.')

        if self._listening_socket is not None:
            self._listening_socket.close()

        if self.socket is not None:
            self.socket.close()
    
    def connected(self):
        '''Check whether or not this socket is connected.

        :returns:
            True if connected. False otherwise.
        '''
        if self.socket is not None:
            return self.socket.connected()
        else:
            return False

    def listen(self, hostname='localhost', port=9000):
        '''Start listening for a connection.
        '''
        logger.debug(
            'SocketListener listening on "%(hostname)s:%(port)i."' % {
                'hostname':hostname,
                'port':port,
            })

        # Create our socket stream to listen on.
        self._listening_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Bind the address.
        self._listening_socket.bind((hostname, port))
        self._listening_socket.listen(1)


class SocketNotEstablishedError(Exception):
    '''Raised if a socket was used before it was connected/established.'''
    pass

