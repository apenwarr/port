#!/usr/bin/env python
# Copyright 2011-2012 Avery Pennarun and port.py contributors.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#    1. Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in
#       the documentation and/or other materials provided with the
#       distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
import array
import errno
import fcntl
import os
import random
import select
import sys
import termios
import time
import tty
import options

optspec = """
port [options...] <tty>
--
s,speed=    the baud rate to use [115200]
l,limit=    maximum upload rate (for devices with crappy flow control) [9600]
"""


def log(s, *args):
    if args:
        ss = s % args
    else:
        ss = s
    sys.stdout.flush()
    sys.stderr.write(ss.replace('\n', '\r\n'))
    sys.stderr.flush()


class ModemError(Exception):
    pass

class AlreadyLockedError(Exception):
    pass


def _speedv(speed):
    try:
        return termios.__dict__['B%s' % int(speed)]
    except KeyError:
        raise ModemError('invalid port speed: %r (try 115200, 57600, etc)'
                         % speed)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError, e:
        if e.errno == errno.ENOENT:
            return  # it's deleted, so that's not an error
        raise


class Lock(object):
    """Represents a unix tty lockfile to prevent overlapping access."""

    def __init__(self, devname):
        assert '/' not in devname
        if os.path.exists('/var/lock'):
            # Linux standard location
            self.path = '/var/lock/LCK..%s' % devname
        else:
            # this is the patch minicom seems to use on MacOS X
            self.path = '/tmp/LCK..%s' % devname
        self.lock()

    def __del__(self):
        self.unlock()

    def read(self):
        try:
            return int(open(self.path).read().strip().split()[0])
        except IOError, e:
            if e.errno == errno.ENOENT:
                return None  # not locked
            else:
                return 0  # invalid lock
        except ValueError:
            return 0

    def _pid_exists(self, pid):
        assert pid > 0
        try:
            os.kill(pid, 0)  # 0 is a signal that always does nothing
        except OSError, e:
            if e.errno == errno.EPERM:  # no permission means it exists!
                return True
            if e.errno == errno.ESRCH:  # not found
                return False
            raise  # any other error is weird, pass it on
        return True  # no error means it exists

    def _try_lock(self):
        try:
            fd = os.open(self.path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0666)
        except OSError:
            return
        try:
            os.write(fd, '%s\n' % os.getpid())
        finally:
            os.close(fd)

    def lock(self):
        mypid = os.getpid()
        for _ in range(10):
            pid = self.read()
            if pid == mypid:
                return
            elif pid is None:
                # file did not exist
                self._try_lock()
            elif pid > 0 and self._pid_exists(pid):
                raise AlreadyLockedError('%r locked by pid %d'
                                         % (self.path, pid))
            else:
                # the lock owner died or didn't write a pid.  Cleaning it
                # creates a race condition.  Delete it only after
                # double checking.
                time.sleep(0.2 + 0.2*random.random())
                pid2 = self.read()
                if pid2 == pid and (pid == 0 or not self._pid_exists(pid)):
                    _unlink(self.path)
                # now loop and try again.  Someone else might be racing with
                # us, so there's no guarantee we'll get the lock on our
                # next try.
        raise AlreadyLockedError('%r lock contention detected' % self.path)

    def unlock(self):
        if self.read() == os.getpid():
            _unlink(self.path)


class Modem(object):
    def __init__(self, filename, speed):
        self.fd = self.tc_orig = None
        if '/' not in filename and os.path.exists('/dev/%s' % filename):
            filename = '/dev/%s' % filename
        self.lock = Lock(os.path.basename(filename))
        self.fd = os.open(filename, os.O_RDWR | os.O_NONBLOCK)
        fcntl.fcntl(self.fd, fcntl.F_SETFL,
                    fcntl.fcntl(self.fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
        self.tc_orig = tc = termios.tcgetattr(self.fd)
        tc[4] = tc[5] = _speedv(speed)
        tc[2] &= ~(termios.PARENB | termios.PARODD)
        tc[2] |= termios.CLOCAL
        termios.tcsetattr(self.fd, termios.TCSADRAIN, tc)
        tty.setraw(self.fd)

    def __del__(self):
        self.close()

    def close(self):
        if self.fd is not None:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.tc_orig)
            except:
                pass
            os.close(self.fd)

    def flags(self):
        bits = [(i, getattr(termios,i))
                for i in dir(termios)
                if i.startswith('TIOCM_')]
        tbuf = array.array('i', [0])
        fcntl.ioctl(self.fd, termios.TIOCMGET, tbuf, True)
        out = []
        for name, bit in sorted(bits):
            if tbuf[0] & bit:
                out.append(name[6:])
        return ', '.join(out)

    def sendbreak(self):
        termios.tcsendbreak(self.fd, 0)


def main():
    o = options.Options(optspec)
    (opt, flags, extra) = o.parse(sys.argv[1:])
    if len(extra) != 1:
        o.fatal("exactly one tty name expected")
    filename = extra[0]
    if opt.limit and opt.limit < 300:
        o.fatal('--limit should be at least 300 bps')
    if opt.limit > max(115200, int(opt.speed)):
        o.fatal('--limit should be no more than --speed')

    tc_stdin_orig = termios.tcgetattr(0)
    modem = Modem(filename, opt.speed)

    line = ''
    MAGIC = ['~.', '!.']

    try:
        tty.setraw(0)

        mflags = None
        last_out = 0
        if opt.limit:
            secs_per_byte = 1.0 / (float(opt.limit) / 10)
            assert(secs_per_byte < 0.1)
        log('(Type ~. or !. to exit, or ~b to send BREAK)')

        while 1:
            newflags = modem.flags()
            if newflags != mflags:
                mflags = newflags
                log('\n(Line Status: %s)\n', mflags)

            r,w,x = select.select([0,modem.fd], [], [])
            if 0 in r:
                buf = os.read(0, 1)
                if buf in '\r\n\x03':
                    line = ''
                else:
                    line += buf
                if line in MAGIC:
                    break
                if line == '~b':
                    log('(BREAK)')
                    modem.sendbreak()
                    line = ''
                elif len(buf):
                    os.write(modem.fd, buf)
                    if opt.limit:
                        time.sleep(secs_per_byte)
            if modem.fd in r:
                buf = os.read(modem.fd, 4096)
                if len(buf):
                    os.write(1, buf)
                if buf == '\0':
                    log('\n(received NUL byte)\n')
    finally:
        termios.tcsetattr(0, termios.TCSANOW, tc_stdin_orig)


if __name__ == '__main__':
    try:
        main()
    except AlreadyLockedError, e:
        sys.stderr.write('error: %s\n' % e)
        exit(1)
