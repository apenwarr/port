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


def modem_flags(fd):
    bits = [(i, getattr(termios,i))
            for i in dir(termios)
            if i.startswith('TIOCM_')]
    tbuf = array.array('i', [0])
    fcntl.ioctl(fd, termios.TIOCMGET, tbuf, True)
    out = []
    for name, bit in sorted(bits):
        if tbuf[0] & bit:
            out.append(name[6:])
    return ', '.join(out)
    

def main():
    o = options.Options(optspec)
    (opt, flags, extra) = o.parse(sys.argv[1:])
    if len(extra) != 1:
        o.fatal("exactly one tty name expected")
    filename = extra[0]
    try:
        speedv = termios.__dict__['B%s' % int(opt.speed)]
    except KeyError:
        o.fatal('invalid port speed: %r (try 115200, 57600, etc)' % opt.speed)
    if opt.limit and opt.limit < 300:
        o.fatal('--limit should be at least 300 bps')
    if opt.limit > max(115200, opt.speed):
        o.fatal('--limit should be no more than --speed')

    fd = os.open(filename, os.O_RDWR | os.O_NONBLOCK)
    fcntl.fcntl(fd, fcntl.F_SETFL,
                fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
    tc_stdin_orig = tc_stdin = termios.tcgetattr(0)
    tc_fd_orig = tc_fd = termios.tcgetattr(fd)

    line = ''
    MAGIC='~.'

    try:
        tc_fd[4] = tc_fd[5] = speedv
        tc_fd[2] &= ~(termios.PARENB | termios.PARODD)
        tc_fd[2] |= termios.CLOCAL
        termios.tcsetattr(fd, termios.TCSANOW, tc_fd)
        tty.setraw(fd)
        tty.setraw(0)

        mflags = None
        last_out = 0
        if opt.limit:
            secs_per_byte = 1.0 / (float(opt.limit) / 10)
            assert(secs_per_byte < 0.1)
        log('(Type ~. to exit)')

        while 1:
            newflags = modem_flags(fd)
            if newflags != mflags:
                mflags = newflags
                log('\n(Line Status: %s)\n', mflags)

            r,w,x = select.select([0,fd], [], [])
            if 0 in r:
                buf = os.read(0, 1)
                if buf in '\r\n\x03':
                    line = ''
                else:
                    line += buf
                if line == MAGIC:
                    break
                if len(buf):
                    os.write(fd, buf)
                    if opt.limit:
                        time.sleep(secs_per_byte)
            if fd in r:
                buf = os.read(fd, 4096)
                if len(buf):
                    os.write(1, buf)
                if buf == '\0':
                    log('\n(received NUL byte)\n')
    finally:
        termios.tcsetattr(0, termios.TCSANOW, tc_stdin_orig)


if __name__ == '__main__':
    main()
