###############################################################################
#
# Copyright (C) 2017 Andrew Muzikin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################

import logging
#logging.basicConfig(format='%(name)s: %(message)s')
import multiprocessing

#import itertools
import zmq

#import time
#from datetime import timedelta


class BTgymDataFeedServer(multiprocessing.Process):
    """
    Data provider server class.
    Enables efficient data sampling for asynchronous multiply BTgym environments execution.
    """
    process = None
    dataset_stat = None

    def __init__(self, dataset=None, network_address=None, log_level=None, task=0):
        """
        Configures data server instance.

        Args:
            dataset:            data domain instance;
            network_address:    ...to bind to.
            log_level:          int, logbook.level
            task:               id
        """
        super(BTgymDataFeedServer, self).__init__()

        self.log_level = log_level
        self.task = task
        self.log = None
        self.dataset = dataset
        self.network_address = network_address

    def run(self):
        """
        Server process runtime body.
        """
        # Logging:
        from logbook import Logger, StreamHandler, WARNING
        import sys
        StreamHandler(sys.stdout).push_application()
        if self.log_level is None:
            self.log_level = WARNING
        self.log = Logger('BTgym_DataServer_{}'.format(self.task), level=self.log_level)

        self.process = multiprocessing.current_process()
        self.log.info('PID: {}'.format(self.process.pid))

        # Set up a comm. channel for server as ZMQ socket:
        context = zmq.Context()
        socket = context.socket(zmq.REP)
        socket.bind(self.network_address)

        # Actually load data to BTgymDataset instance, will reset it later on:
        try:
            assert not self.dataset.data.empty

        except (AssertionError, AttributeError) as e:
            self.dataset.read_csv()

        # Describe dataset:
        self.dataset_stat = self.dataset.describe()

        local_step = 0
        fresh_sample = False

        # Main loop:
        while True:
            self.log.debug('Domain_dataset_ready: {}, fresh_sample: {}'.format(self.dataset.is_ready, fresh_sample))
            if not fresh_sample:
                if self.dataset.is_ready:
                    # Get sample:
                    sample = self.dataset.sample()
                    data_dict = dict(
                        sample=sample,
                        dataset_stat=self.dataset_stat,
                        local_step=local_step,
                    )
                    fresh_sample = True

                else:
                    # Dataset not ready, make dummy:
                    data_dict = dict(
                        sample=None,
                        dataset_stat=self.dataset_stat,
                        local_step=local_step,
                    )

            # Stick here with episode data in hand until get request:
            service_input = socket.recv_pyobj()
            msg = 'Received <{}>'.format(service_input)
            self.log.debug(msg)

            if 'ctrl' in service_input:
                # It's time to exit:
                if service_input['ctrl'] == '_stop':
                    # Server shutdown logic:
                    # send last run statistic, release comm channel and exit:
                    message = {'ctrl': 'Exiting.'}
                    self.log.info(str(message))
                    socket.send_pyobj(message)
                    socket.close()
                    context.destroy()
                    return None

                # Reset datafeed:
                elif service_input['ctrl'] == '_reset_data':
                    try:
                        kwargs = service_input['kwargs']

                    except KeyError:
                        kwargs = {}

                    self.dataset.reset(**kwargs)
                    message = {'ctrl': 'Reset with kwargs: {}'.format(kwargs)}
                    self.log.debug('Sent: ' + str(message))
                    self.log.debug('Data_is_ready: {}'.format(self.dataset.is_ready))
                    socket.send_pyobj(message)
                    fresh_sample = False

                # Send episode datafeed:
                elif service_input['ctrl'] == '_get_data':
                    if self.dataset.is_ready:
                        message = 'Sending subset_#{} data {}.'.format(local_step, data_dict)
                        self.log.debug(message)
                        socket.send_pyobj(data_dict)
                        local_step += 1

                    else:
                        message = {'ctrl': 'Dataset not ready, waiting for control key <_reset_data>'}
                        self.log.debug('Sent: ' + str(message))
                        socket.send_pyobj(message)  # pairs any other input
                    # Mark current sample as used anyway:
                    fresh_sample = False

                # Send dataset statisitc:
                elif service_input['ctrl'] == '_get_info':
                    message = 'Sending info for #{}.'.format(local_step)
                    self.log.debug(message)
                    # Compose response:
                    info_dict = dict(
                        dataset_stat=self.dataset_stat,
                        dataset_columns=list(self.dataset.names),
                        pid=self.process.pid,
                        dataset_is_ready=self.dataset.is_ready
                    )
                    socket.send_pyobj(info_dict)

                else:  # ignore any other input
                    # NOTE: response dictionary must include 'ctrl' key
                    message = {'ctrl': 'waiting for control keys:  <_reset_data>, <_get_data>, <_get_info>, <_stop>.'}
                    self.log.debug('Sent: ' + str(message))
                    socket.send_pyobj(message)  # pairs any other input

            else:
                message = {'ctrl': 'No <ctrl> key received, got:\n{}'.format(msg)}
                self.log.debug(str(message))
                socket.send_pyobj(message) # pairs input
