#!/usr/bin/env python
import sys, os, tty, termios, select
import options

optspec = """
port <tty> <speed>
"""
o = options.Options(optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

if len(extra) != 2:
    o.fatal("exactly two arguments expected")

(filename, speed) = extra

try:
    speedv = termios.__dict__['B%s' % speed]
except KeyError:
    o.fatal('invalid port speed: %r (try 115200, 57600, etc)' % speed)


fd = os.open(filename, os.O_RDWR)
tc_stdin_orig = tc_stdin = termios.tcgetattr(0)
tc_fd_orig = tc_fd = termios.tcgetattr(fd)

line = ''
MAGIC='~.'

try:
    tc_fd[4] = tc_fd[5] = speedv
    tc_fd[2] &= ~(termios.PARENB | termios.PARODD)
    termios.tcsetattr(fd, termios.TCSANOW, tc_fd)
    tty.setraw(fd)
    tty.setraw(0)

    sys.stderr.write('(Type ~ to exit)\r\n')
    
    while 1:
        r,w,x = select.select([0,fd], [], [])
        if 0 in r:
            buf = os.read(0, 1)
            if buf in '\r\n':
                line = ''
            else:
                line += buf
            if line == MAGIC:
                break
            if len(buf):
                os.write(fd, buf)
            #sys.stderr.write('<%r>' % buf)
        if fd in r:
            buf = os.read(fd, 4096)
            if len(buf):
                os.write(1, buf)
            #sys.stderr.write('[%r]' % buf)
finally:
    termios.tcsetattr(0, termios.TCSANOW, tc_stdin_orig)
