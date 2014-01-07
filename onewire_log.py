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

import csv
import errno
import glob
import os
import re
import sys
import time
import optparse

# How often to poll sensors (seconds)
POLL_INTERVAL=30

# Output filename format (file will be appended if exists)
# Any subdirectories will be created as needed.
OUTPUT_FILE="onewire_logs/w1_%Y%m%d_%H%M%S.csv"

__version__ = "0.1"

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
        return False
    return maybe_modprobe(auto_ok=True)


def main():
    parser = optparse.OptionParser(
        version=__version__)
    parser.add_option('-v', '--verbose', action='store_true',
                      help='Print every reading to screen (as well as to file)')
    parser.add_option('-o', '--output', metavar='PATTERN',
                      default=OUTPUT_FILE,
                      help='Output filename (with strftime elements). '
                      'Set to empty string to disable. Default %default')
    parser.add_option('-p', '--poll-interval', type='float', metavar='SEC',
                      default=POLL_INTERVAL,
                      help='Poll interval. Default %default sec.')
    parser.add_option('-m', '--modprobe', action='store_true',
                      help='Run "sudo modprobe..." if needed')
    
    opts, args = parser.parse_args()
    if len(args):
        parser.error('No positional arguments accepted')

    if not maybe_modprobe(auto_ok=opts.modprobe):
        return 1

    print 'Polling one-wire devices every %.1f seconds' % opts.poll_interval

    active_names = list()
    active_file = active_writer = None

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
            print '%s Active devices are: %s' % (
                time.strftime("%F %T", ltime),
                ', '.join(names) or 'NONE')
            active_names = names
            # Close and re-open file so we can write new header
            if active_file:
                active_file.close()
            active_file = active_writer = None

        if opts.output and not active_file:
            new_name = time.strftime(opts.output, ltime)
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

        if active_writer:
            active_writer.writerow(
                dict(date=time.strftime('%F', ltime),
                     time=time.strftime('%T', ltime),
                     ts=str(int(ts)),
                     **data))
            active_file.flush()

        if opts.verbose:            
            print "%s DATA %s" % (
                time.strftime("%T", ltime),
                (' '.join(str(data.get(n, 'no-data'))
                          for n in active_names)
                 or 'no data'))        

        sys.stdout.flush()



if __name__ == '__main__':
    sys.exit(main())
