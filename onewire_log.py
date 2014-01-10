#!/usr/bin/env python
# Copyright (C) 2014 Mikhail Afanasyev
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>
"""
This program polls 1-wire devices via Linux supported adapter such as 
DS1490/DS9490. It supports any thermometer which is supported by Linux
kernel (google w1_therm.c for details)

To run this program on startup:
(1) run: sudoedit /etc/modules
    Add "w1_therm"  to the end of list
(2) run: crontab -e
    Add the following line:
@reboot          /home/USER/PATH-TO/onewire_log.py -q -o ~/onewire_logs/
    Maybe add the following line to auto-restart program if it crashes:
2-59/20 * * * *  /home/USER/PATH-TO/onewire_log.py -q -o ~/onewire_logs/
"""

import csv
import errno
import glob
import optparse
import os
import re
import socket
import sys
import time

# How often to poll sensors (seconds)
POLL_INTERVAL=30

# Output filename format (file will be appended if exists)
# Any subdirectories will be created as needed.
DEFAULT_OUTPUT="onewire_logs/w1_%Y%m%d_%H%M%S.csv"

# In order to ensure only one instance of this app is running,
# we will bind to a port. This selects a port number.
UNIQUE_PORT = 25165

__version__ = "0.2"

def get_data():
    result = dict()
    for fname in sorted(glob.glob("/sys/bus/w1/devices/*/w1_slave")):
        dname = os.path.basename(os.path.dirname(fname))
        try:
            with open(fname, 'r') as fh:
                content = fh.read(1024)
        except IOError as e:
            if e.errno == errno.ENOENT:
                print >>sys.stderr, 'File disappeared: %r' % fname
                continue
            raise
        if not re.search(' crc=.. YES', content):
            if 'ff ff ff ff ff ff ff ff ff' in content:
                print >>sys.stderr, 'No response from sensor %r' % dname
                result[dname] = 'no-data'
            else:
                print >>sys.stderr, 'Invalid CRC from sensor %r' % dname
                result[dname] = 'badcrc'
            continue
        mm = re.search('t=(-?[0-9]*)', content)
        if mm:
            result[dname] = "%.3f" % (int(mm.group(1))/1000.0)
            continue

        print >>sys.stderr, 'Cannot find temperature on output from sensor %r' % dname
        print >>sys.stderr, repr(content)
    return result

def maybe_modprobe(auto_ok=True):
    if os.path.exists('/sys/module/w1_therm'):
        return True
    print >>sys.stderr, 'Thermometer module not loaded'
    cmd = 'sudo modprobe w1_therm'
    if auto_ok:
        print >>sys.stderr, 'Loading module'
        os.system(cmd)
    else:
        print >>sys.stderr, 'Run this command to load module:'
        print >>sys.stderr, ' ', cmd
        print >>sys.stderr, ' or try: %s -m' % (os.path.basename(__file__))
        return False
    return maybe_modprobe(auto_ok=True)


def main():
    parser = optparse.OptionParser(
        version=__version__, description=__doc__)
    parser.formatter.format_description = str.lstrip

    parser.add_option('-v', '--verbose', action='store_true',
                      help='Print every reading to screen (as well as to file)')
    parser.add_option('-q', '--quiet', action='store_true',
                      help='Print less output (in particular, do not print '
                      'anything if another instance is already running')
    parser.add_option('-o', '--output', metavar='FNAME',
                      default=DEFAULT_OUTPUT,
                      help='Output filename (with strftime elements). '
                      'Set to empty string to disable. Default %default. '
                      'If ends with /, default filename is appended.')
    parser.add_option('-p', '--poll-interval', type='float', metavar='SEC',
                      default=POLL_INTERVAL,
                      help='Poll interval. Default %default sec.')
    parser.add_option('-m', '--modprobe', action='store_true',
                      help='Run "sudo modprobe..." if needed')
    parser.add_option('--daily', action='store_true',
                      help='Start new file every midnight')
    
    opts, args = parser.parse_args()
    if len(args):
        parser.error('No positional arguments accepted')

    if not maybe_modprobe(auto_ok=opts.modprobe):
        return 1

    output = os.path.expanduser(opts.output)
    if output.endswith('/'):
        output += os.path.basename(DEFAULT_OUTPUT)

    active_names = list()
    active_file = active_writer = None
    active_date = None

    unique_socket = socket.socket(socket.SOCK_DGRAM)
    try:
        unique_socket.bind(('127.0.0.1', UNIQUE_PORT))
    except socket.error as e:
        if e.errno != errno.EADDRINUSE:
            raise
        if not opts.quiet:
            print 'Error: another instance of this program is already running'
        return 0
    
    if not opts.quiet:
        print 'Polling one-wire devices every %.1f seconds' % opts.poll_interval

    while True:
        # Sleep. Try to maintain interval even if polling takes up to 75% of it,
        # and try to keep (time % poll_interval) close to zero for nice timestamps.
        next_slot = int(time.time() / opts.poll_interval + 1) * opts.poll_interval
        delay = max(opts.poll_interval / 4.0, 
                    min(opts.poll_interval * 1.5, next_slot - time.time()))
        time.sleep(delay)

        ts = time.time()
        ltime = time.localtime(ts)
        data = get_data()

        names = sorted(data.keys())
        if names != active_names:
            if not opts.quiet:
                print '%s Active devices are: %s' % (
                    time.strftime("%F %T", ltime), ', '.join(names) or 'NONE')
            active_names = names
            # Close and re-open file so we can write new header
            if active_file:
                active_file.close()
            active_file = active_writer = None

        if (opts.daily and active_file and 
            active_date != time.strftime('%F', ltime)):
            if not opts.quiet:
                print 'New day comes. Starting new file'
            if active_file:
                active_file.close()
            active_file = active_writer = None
            
        if output and not active_file:
            new_name = time.strftime(output, ltime)
            if not opts.quiet:
                print 'Writing data to %s' % new_name
            new_dir = os.path.dirname(new_name)
            if new_dir:
                try: os.makedirs(new_dir)
                except OSError as e:
                    if e.errno != errno.EEXIST:
                        raise
                    
            active_file = open(new_name, 'a')
            active_writer = csv.DictWriter(
                active_file,
                ['date', 'time', 'ts'] + active_names)
            active_writer.writeheader()
            active_date = time.strftime('%F', ltime)

        if active_writer:
            active_writer.writerow(
                dict(date=time.strftime('%F', ltime),
                     time=time.strftime('%T', ltime),
                     ts=str(int(ts)),
                     **data))
            active_file.flush()

        verbose_msg = (
            "%s DATA %s" % (time.strftime("%T", ltime),
                            (' '.join(str(data.get(n, 'no-data'))
                                      for n in active_names)
                             or 'no data'))
            )

        if opts.verbose:            
            print verbose_msg

        sys.stdout.flush()



if __name__ == '__main__':
    sys.exit(main())
