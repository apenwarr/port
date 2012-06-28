#!/usr/bin/env python
import sys, os, tty, termios, fcntl, select, array, time
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


def _speedv(speed):
    try:
        return termios.__dict__['B%s' % int(speed)]
    except KeyError:
        raise ModemError('invalid port speed: %r (try 115200, 57600, etc)'
                         % speed)


class Modem(object):
    def __init__(self, filename, speed):
        self.fd = self.tc_orig = None
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
    main()
