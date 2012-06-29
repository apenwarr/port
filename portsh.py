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
def assembler(splitter):
    import os, select, subprocess, sys, zlib

    zc = zlib.compressobj()
    zd = zlib.decompressobj()
    def encode(b):
        return ((zc.compress(b) + zc.flush(zlib.Z_SYNC_FLUSH))
                .encode("base64").replace("\n", ""))
    def decode(b):
        try:
            return zd.decompress(b.strip().decode("base64"))
        except Exception:
            sys.stderr.write("ERROR base64 decode: %r\n" % b)
            raise

    cmd = decode(sys.stdin.readline())
    print "%s-RUNNING" % splitter

    p = subprocess.Popen(cmd, shell=True,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)

    fds = [sys.stdin, p.stdout, p.stderr]
    while p.stdout in fds or p.stderr in fds:
        r,w,x = select.select(fds, [], [])
        if p.stderr in r:
            b = os.read(p.stderr.fileno(), 4096)
            if b:
                print "2 %s" % encode(b)
            else:
                fds.remove(p.stderr)
        elif p.stdout in r:
            b = os.read(p.stdout.fileno(), 4096)
            if b:
                print "1 %s" % encode(b)
            else:
                fds.remove(p.stdout)
        elif sys.stdin in r:
            b = os.read(sys.stdin.fileno(), 4096)
            if b:
                p.stdin.write(decode(b))
            else:
                fds.remove(sys.stdin)
                p.stdin.close()
    rv = p.wait()
    print "%s-EXIT-%d" % (splitter, rv)
# END ASSEMBLER
# The above is the stage2 assembler that gets run on the remote
# system. To ensure that syntax errors and exceptions have useful line
# numbers, keep it at the top of the file.

import re, os, sys, tty, termios, fcntl, select, array, time, uuid, zlib
import options
import port

optspec = """
portsh [options...] <tty> <command string...>
--
t,trace     show serial port trace on stderr
s,speed=    the baud rate to use [115200]
u,user=     response to 'login:' prompt [root]
p,password= response to 'Password:' prompt
"""


def log(s, *args):
    if args:
        ss = s % args
    else:
        ss = s
    sys.stdout.flush()
    sys.stderr.write(ss.replace('\n', '\r\n'))
    sys.stderr.flush()


_want_trace = False
def trace(s):
    if _want_trace:
        log('\x1b[35;1m%s\x1b[m' % re.sub(r'\x1b[[\d;]+[a-z]', '', s))


class Reader(object):
    def __init__(self, fd):
        self.fd = fd
        self.buf = ''

    def fill(self, timeout):
        r,w,x = select.select([self.fd], [], [], timeout)
        if r:
            nbuf = os.read(self.fd, 4096)
            if nbuf:
                trace('(%d)' % len(nbuf))
                self.buf += nbuf.replace('\r\n', '\n')
                return nbuf
        return ''

    def get(self, nbytes):
        out = self.buf[:nbytes]
        self.buf = self.buf[nbytes:]
        return out

    def get_until(self, sep):
        pos = self.buf.find(sep)
        if pos >= 0:
            return self.get(pos + len(sep))

    def get_all(self):
        return self.get(len(self.buf))

    def lines(self):
        while 1:
            line = self.get_until('\n')
            if not line:
                break
            yield line


def read_until_idle(fd, start_timeout):
    timeout = start_timeout
    buf = ''
    while 1:
        r,w,x = select.select([fd], [], [], timeout)
        if r:
            nbuf = os.read(fd, 4096)
            if nbuf:
                trace('(%d)' % len(nbuf))
            buf += nbuf
            timeout = 0.1
        else:
            break
    return buf


def get_shell_prompt(fd, user, password):
    # Send some ctrl-c (SIGINTR) and newlines as a basic terminal reset.
    os.write(fd, '\x03\x03\x03\r\n')
    last_was_sh = 0
    buf = read_until_idle(fd, 0.0)
    for tries in range(10):
        trace(buf.replace('\r', ''))
        bufclean = buf.lower().strip()
        if bufclean.endswith('login:'):
            os.write(fd, user + '\n')
        elif bufclean.endswith('password:'):
                os.write(fd, password + '\n')
                trace('(password)')
        elif ('%s%s' % ('MAGIC', 'STRING')) in buf.replace('\r', ''):
            # success!
            trace('(got a shell prompt)\n')
            return
        elif (not last_was_sh and
              (bufclean.endswith('#') or bufclean.endswith('$') or # sh
               bufclean.endswith('%') or bufclean.endswith('>') or # csh/tcsh
               '\x1b' in bufclean)):  # fancy ansi characters
            # probably shell prompt
            os.write(fd, 'printf MAGIC; printf STRING\r')
            trace('(shelltest)\n')
            last_was_sh = 1
        else:
            last_was_sh = 0
            r,w,x = select.select([fd], [], [], 2.0)
            if not r:
                # Send some ctrl-c (SIGINTR), ctrl-d (EOF), and
                #  ctrl-\ (SIGQUIT) to try to exit out of anything
                #  already running.
                trace('(prodding)\n')
                os.write(fd, '\x03\x03\x03\r\n\x04\x04\x04\x1c\x1c\x1c\r\n')
        buf = read_until_idle(fd, 1.0)
    raise port.ModemError("couldn't get a shell prompt after 10 tries")


def wait_for_string(reader, s):
    timeout = 10.0
    for i in range(50):
        nbuf = reader.fill(timeout)
        timeout = 1.0
        trace(nbuf)
        got = reader.get_until(s)
        if got:
            trace('(got %s)' % s)
            return got[:-len(s)]
    raise port.ModemError("didn't find %r after 10 tries")


PY_SCRIPT1 = r"""
stty sane; stty -echo; python -Sc '
import sys, zlib
print "%s-READY\n" % "SPLITTER";
b = sys.stdin.readline().strip()
exec(zlib.decompress(b.decode("base64")))
assembler("SPLITTER")
'; printf %s-EXIT-97\\n SPLITTER; stty sane; cat
"""

def main():
    o = options.Options(optspec)
    (opt, flags, extra) = o.parse(sys.argv[1:])
    if len(extra) < 2:
        o.fatal("exactly one tty name and a command expected")
    if opt.trace:
        global _want_trace
        _want_trace = opt.trace
    filename = extra[0]
    cmd = ' '.join(extra[1:])

    modem = port.Modem(filename, opt.speed)
    get_shell_prompt(modem.fd, opt.user, opt.password or '')

    splitter = uuid.uuid4().hex
    reader = Reader(modem.fd)

    zc = zlib.compressobj()
    zd = zlib.decompressobj()
    def encode(b):
        return ((zc.compress(b) + zc.flush(zlib.Z_SYNC_FLUSH))
                .encode('base64').replace('\n', ''))
    def decode(b):
        return zd.decompress(b.strip().decode('base64'))

    os.write(modem.fd,
             "%s\r" % PY_SCRIPT1.strip().replace('SPLITTER', splitter))
    wait_for_string(reader, '%s-READY\n' % splitter)

    py_script, junk = open(__file__).read().split('# END ASSEMBLER\n', 1)
    assert junk
    cpy_script = zlib.compress(py_script).encode('base64').replace('\n', '')
    trace('(cpy_script=%d)' % len(cpy_script))
    os.write(modem.fd, "%s\r" % cpy_script)
    zc = zlib.compressobj()
    os.write(modem.fd, "%s\r" % encode(cmd))
    wait_for_string(reader, '%s-RUNNING\n' % splitter)
    split_end = '%s-EXIT-' % splitter

    fds = [0, modem.fd]
    while 1:
        r,w,x = select.select(fds, [], [])
        if 0 in r:
            buf = os.read(0, 128)
            if len(buf):
                trace('>>%s' % buf)
                os.write(modem.fd, encode(buf) + '\n')
            else:
                os.write(modem.fd, '\n\x04')  # EOF signal
                fds.remove(0)
        if modem.fd in r:
            nbuf = 1
            while nbuf:
                nbuf = reader.fill(0.1)
                trace(nbuf)
                for line in reader.lines():
                    if split_end in line:
                        pre, rv = line.split(split_end, 1)
                        assert not pre
                        trace('(rv=%r)' % rv)
                        sys.exit(int(rv))
                    if line.startswith('1 '):
                        os.write(1, decode(line[2:]))
                    elif line.startswith('2 '):
                        os.write(2, decode(line[2:]))
                    elif (line.startswith('Traceback ') or
                          line.startswith('ERROR')):
                        log(line)
                        while nbuf:
                            nbuf = reader.fill(1)
                            log(nbuf)
                    else:
                        while nbuf:
                            nbuf = reader.fill(0.1)
                            trace(nbuf)
                        raise port.ModemError('unexpected prefix %r...'
                                              % line[:15])


if __name__ == '__main__':
    main()
