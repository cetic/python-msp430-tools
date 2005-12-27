#!/usr/bin/env python
# Parallel JTAG programmer for the MSP430 embedded proccessor.
#
# (C) 2002-2004 Chris Liechti <cliechti@gmx.net>
# this is distributed under a free software license, see license.txt
#
# http://mspgcc.sf.net
#
# Requires Python 2+ and the binary extension _parjtag or ctypes
# and MSP430mspgcc.dll/libMSP430mspgcc.so and HIL.dll/libHIL.so
#
# $Id: msp430-jtag.py,v 1.13 2005/12/27 14:58:27 cliechti Exp $

import sys
from msp430.util import hexdump, makeihex
from msp430 import memory, jtag


VERSION = "2.1"

DEBUG = 0                           #disable debug messages by default

#enumeration of output formats for uploads
HEX             = 0
INTELHEX        = 1
BINARY          = 2


def usage():
    """print some help message"""
    sys.stderr.write("""
USAGE: %s [options] [file]
Version: %s

If "-" is specified as file the data is read from stdin.
A file ending with ".txt" is considered to be in TI-Text format all
other filenames are considered to be in IntelHex format.

General options:
  -h, --help            Show this help screen.
  -l, --lpt=name        Specify an other parallel port.
                        (defaults to LPT1 (/dev/parport0 on unix))
  -D, --debug           Increase level of debug messages. This won't be
                        very useful for the average user.
  -I, --intelhex        Force fileformat to IntelHex
  -T, --titext          Force fileformat to be TI-Text
  -R, --ramsize         Specify the amount of RAM to be used to program
                        flash (default 256).

Funclets:
  -f, --funclet         The given file is a funclet (a small program to
                        be run in RAM).
  --parameter=<key>=<value>   Pass parameters to funclets.
                        Registers can be written like "R15=123" or "R4=0x55"
                        A string can be written to memory with "0x2e0=hello"
                        --parameter can be given more than once
  --result=value        Read results from funclets. "Rall" read all registers
                        (case insensitive) "R15" reads R15 etc. Address ranges
                        can be read with "0x2e0-0x2ff". see also --upload.
                        --result can be given more than once
  --timeout=value       Abort the funclet after the given time in seconds
                        if it does not exit no itslef. (default 1)

Note: writing and/or reading RAM before and/or after running a funclet may not
work as expected on devices with the JTAG bug like the F123.

Program flow specifiers:

  -e, --masserase       Mass Erase (clear all flash memory)
                        Note: SegmentA on F2xx is NOT erased, that must be
                        done separately with --erase=0x1000
  -m, --mainerase       Erase main flash memory only
  --eraseinfo           Erase info flash memory only (0x1000-0x10ff)
  --erase=address       Selectively erase segment at the specified address
  --erase=adr1-adr2     Selectively erase a range of segments
  -E, --erasecheck      Erase Check by file
  -p, --program         Program file
  -v, --verify          Verify by file

The order of the above options matters! The table is ordered by normal
execution order. For the options "Epv" a file must be specified.
Program flow specifiers default to "p" if a file is given.
Don't forget to specify "e", "eE" or "m" when programming flash!
"p" already verifies the programmed data, "v" adds an additional
verification through uploading the written data for a 1:1 compare.
No default action is taken if "p" and/or "v" is given, say specifying
only "v" does a "check by file" of a programmed device.

Data retreiving:
  -u, --upload=addr     Upload a datablock (see also: -s).
  -s, --size=num        Size of the data block do upload. (Default is 2)
  -x, --hex             Show a hexadecimal display of the uploaded data.
                        (Default)
  -b, --bin             Get binary uploaded data. This can be used
                        to redirect the output into a file.
  -i, --ihex            Uploaded data is output in Intel HEX format.
                        This can be used to clone a device.

Do before exit:
  -g, --go=address      Start programm execution at specified address.
                        This implies option "w" (wait)
  -r, --reset           Reset connected MSP430. Starts application.
                        This is a normal device reset and will start
                        the programm that is specified in the reset
                        interrupt vector. (see also -g)
  -w, --wait            Wait for <ENTER> before closing parallel port.

Address parameters for --erase, --upload, --size can be given in
decimal, hexadecimal or octal.
""" % (sys.argv[0], VERSION))


def main():
    global DEBUG
    import getopt
    filetype    = None
    filename    = None
    reset       = 0
    wait        = 0
    goaddr      = None
    jtagobj     = jtag.JTAG()
    toinit      = []
    todo        = []
    startaddr   = None
    size        = 2
    outputformat= HEX
    lpt         = None
    funclet     = None
    ramsize     = None
    do_close    = 1
    parameters  = []
    results     = []
    timeout     = 1

    sys.stderr.write("MSP430 parallel JTAG programmer Version: %s\n" % VERSION)
    try:
        opts, args = getopt.getopt(sys.argv[1:],
            "hl:weEmpvrg:Du:d:s:xbiITfR:S",
            ["help", "lpt=", "wait"
             "masserase", "erasecheck", "mainerase", "program",
             "erase=", "eraseinfo",
             "verify", "reset", "go=", "debug",
             "upload=", "download=", "size=", "hex", "bin", "ihex",
             "intelhex", "titext", "funclet", "ramsize=", "progress",
             "no-close", "parameter=", "result=", "timeout="]
        )
    except getopt.GetoptError, e:
        # print help information and exit:
        sys.stderr.write("\nError in argument list: %s!\n" % e)
        usage()
        sys.exit(2)

    for o, a in opts:
        if o in ("-h", "--help"):
            usage()
            sys.exit()
        elif o in ("-l", "--lpt"):
            lpt = a
        elif o in ("-w", "--wait"):
            wait = 1
        elif o in ("-e", "--masserase"):
            toinit.append(jtagobj.actionMassErase)      #Erase Flash
        elif o in ("-E", "--erasecheck"):
            toinit.append(jtagobj.actionEraseCheck)     #Erase Check (by file)
        elif o in ("-m", "--mainerase"):
            toinit.append(jtagobj.actionMainErase)      #Erase main Flash
        elif o == "--erase":
            if '-' in a:
                adr, adr2 = a.split('-', 1)
                try:
                    adr = int(adr, 0)
                except ValueError:
                    sys.stderr.write("Address range start address must be a valid number in dec, hex or octal\n")
                    sys.exit(2)
                try:
                    adr2 = int(adr2, 0)
                except ValueError:
                    sys.stderr.write("Address range end address must be a valid number in dec, hex or octal\n")
                    sys.exit(2)
                while adr <= adr2:
                    if not (0x1000 <= adr <= 0xffff):
                        sys.stderr.write("Start address is not within Flash memory\n")
                        sys.exit(2)
                    elif adr < 0x1100:
                        modulo = 64     #F2xx XXX: on F1xx/F4xx are segments erased twice
                    elif adr < 0x1200:
                        modulo = 256
                    else:
                        modulo = 512
                    adr = adr - (adr % modulo)
                    toinit.append(jtagobj.makeActionSegmentErase(adr))
                    adr = adr + modulo
            else:
                try:
                    seg = int(a, 0)
                    toinit.append(jtagobj.makeActionSegmentErase(seg))
                except ValueError:
                    sys.stderr.write("Segment address must be a valid number in dec, hex or octal or a range adr1-adr2\n")
                    sys.exit(2)
        elif o == "--eraseinfo":
            #F2xx XXX: on F1xx/F4xx are segments erased twice
            toinit.append(jtagobj.makeActionSegmentErase(0x1000))
            toinit.append(jtagobj.makeActionSegmentErase(0x1040))
            toinit.append(jtagobj.makeActionSegmentErase(0x1080))
            toinit.append(jtagobj.makeActionSegmentErase(0x10c0))
        elif o in ("-p", "--program"):
            todo.append(jtagobj.actionProgram)          #Program file
        elif o in ("-v", "--verify"):
            todo.append(jtagobj.actionVerify)           #Verify file
        elif o in ("-r", "--reset"):
            reset = 1
        elif o in ("-g", "--go"):
            try:
                goaddr = int(a, 0)                      #try to convert decimal
            except ValueError:
                sys.stderr.write("Start address must be a valid number in dec, hex or octal\n")
                sys.exit(2)
        elif o in ("-D", "--debug"):
            DEBUG = DEBUG + 1
            try:
                jtagobj.setDebugLevel(DEBUG)
            except IOError:
                sys.stderr.write("Failed to set debug level in backend library\n")
            memory.DEBUG = memory.DEBUG + 1
        elif o in ("-u", "--upload"):
            try:
                startaddr = int(a, 0)                   #try to convert number of any base
            except ValueError:
                sys.stderr.write("Upload address must be a valid number in dec, hex or octal\n")
                sys.exit(2)
        elif o in ("-s", "--size"):
            try:
                size = int(a, 0)
            except ValueError:
                sys.stderr.write("Size must be a valid number in dec, hex or octal\n")
                sys.exit(2)
        #outut formats
        elif o in ("-x", "--hex"):
            outputformat = HEX
        elif o in ("-b", "--bin"):
            outputformat = BINARY
        elif o in ("-i", "--ihex"):
            outputformat = INTELHEX
        #input formats
        elif o in ("-I", "--intelhex"):
            filetype = 0
        elif o in ("-T", "--titext"):
            filetype = 1
        #others
        elif o in ("-f", "--funclet"):
            funclet = 1
        elif o in ("-R", "--ramsize"):
            try:
                ramsize = int(a, 0)
            except ValueError:
                sys.stderr.write("Ramsize must be a valid number in dec, hex or octal\n")
                sys.exit(2)
        elif o in ("-S", "--progress"):
            jtagobj.showprogess = 1
        elif o in ("--no-close", ):
            do_close = 0
        elif o in ("--parameter", ):
            if '=' in a:
                key, value = a.lower().split('=', 2)
                if key[0] == 'r':
                    regnum = int(key[1:])
                    value = int(value, 0)
                    parameters.append((jtagobj.setCPURegister, (regnum, value)))
                else:
                    address = int(key,0)
                    parameters.append((jtagobj.downloadData, (address, value)))
            else:
                sys.stderr.write("Expected <key>=<value> pair in --parameter option, but no '=' found.\n")
                sys.exit(2)
        elif o in ("--result", ):
            a = a.lower()
            if a == 'rall':
                for regnum in range(16):
                    results.append(('R%-2d = 0x%%04x' % regnum, jtagobj.getCPURegister, (regnum,)))
            elif a[0] == 'r':
                regnum = int(a[1:])
                results.append(('R%-2d = 0x%%04x' % regnum, jtagobj.getCPURegister, (regnum,)))
            else:
                try:
                    if '-' in a:
                        start, end = a.split('-', 2)
                        start = int(start, 0)
                        end = int(end, 0)
                    else:
                        start = end = int(a,0)
                except ValueError:
                    raise ValueError("--result: Addresses or address ranges must be dec, hex or octal")
                results.append(('0x%04x: %%r' % start, jtagobj.uploadData, (start, end-start)))
        elif o in ("--timeout", ):
            timeout = float(a)

    if len(args) == 0:
        sys.stderr.write("Use -h for help\n")
    elif len(args) == 1:                                #a filename is given
        if not funclet:
            if not todo:                                #if there are no actions yet
                todo.extend([                           #add some useful actions...
                    jtagobj.actionProgram,
                ])
        filename = args[0]
    else:                                               #number of args is wrong
        sys.stderr.write("\nUnsuitable number of arguments\n")
        usage()
        sys.exit(2)

    if DEBUG:   #debug infos
        sys.stderr.write("Debug is level set to %d\n" % DEBUG)
        sys.stderr.write("Python version: %s\n" % sys.version)
        sys.stderr.write("JTAG backend: %s\n" % jtag.backend)


    #sanity check of options
    if goaddr and reset:
        sys.stderr.write("Warning: option --reset ignored as --go is specified!\n")
        reset = 0

    if startaddr and reset:
        sys.stderr.write("Warning: option --reset ignored as --upload is specified!\n")
        reset = 0
        
    if startaddr and wait:
        sys.stderr.write("Warning: option --wait ignored as --upload is specified!\n")
        wait = 0

    #prepare data to download
    jtagobj.data = memory.Memory()                      #prepare downloaded data
    if filetype is not None:                            #if the filetype is given...
        if filename is None:
            raise ValueError("No filename but filetype specified")
        if filename == '-':                             #get data from stdin
            file = sys.stdin
        else:
            file = open(filename,"rb")                  #or from a file
        if filetype == 0:                               #select load function
            jtagobj.data.loadIHex(file)                 #intel hex
        elif filetype == 1:
            jtagobj.data.loadTIText(file)               #TI's format
        else:
            raise ValueError("Illegal filetype specified")
    else:                                               #no filetype given...
        if filename == '-':                             #for stdin:
            jtagobj.data.loadIHex(sys.stdin)            #assume intel hex
        elif filename:
            jtagobj.data.loadFile(filename)             #autodetect otherwise

    if DEBUG > 5: sys.stderr.write("File: %r\n" % filename)

    if toinit:
        if DEBUG > 0:       #debug
            #show a nice list of sheduled actions
            sys.stderr.write("TOINIT list:\n")
            for f in toinit:
                try:
                    sys.stderr.write("   %s\n" % f.func_name)
                except AttributeError:
                    sys.stderr.write("   %r\n" % f)
    if todo:
        if DEBUG > 0:       #debug
            #show a nice list of sheduled actions
            sys.stderr.write("TODO list:\n")
            for f in todo:
                try:
                    sys.stderr.write("   %s\n" % f.func_name)
                except AttributeError:
                    sys.stderr.write("   %r\n" % f)

    sys.stderr.flush()

    jtagobj.connect(lpt)                                #try to open port
    abort_due_to_error = 1
    try:
        if ramsize is not None:
            jtagobj.setRamsize(ramsize)
        #initialization list
        if toinit:  #erase and erase check
            if DEBUG: sys.stderr.write("Preparing device ...\n")
            for f in toinit: f()

        #work list
        if todo:
            for f in todo: f()                          #work through todo list

        if reset:                                       #reset device first if desired
            jtagobj.reset()

        for function, args in parameters:
            function(*args)
        
        if funclet is not None:                         #download and start funclet
            jtagobj.actionFunclet(timeout)

        if goaddr is not None:                          #start user programm at specified address
            jtagobj.actionRun(goaddr)                   #load PC and execute

        for format, function, args in results:
            print format % function(*args)

        #upload datablock and output
        if startaddr is not None:
            if goaddr:                                  #if a program was started...
                raise NotImplementedError
                #TODO:
                #sys.stderr.write("Waiting to device for reconnect for upload: ")
            data = jtagobj.uploadData(startaddr, size)  #upload data
            if outputformat == HEX:                     #depending on output format
                hexdump( (startaddr, data) )            #print a hex display
            elif outputformat == INTELHEX:
                makeihex( (startaddr, data) )           #ouput a intel-hex file
            else:
                if sys.platform == "win32":
                    #ensure that the console is in binary mode
                    import os, msvcrt
                    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
                
                sys.stdout.write(data)                  #binary output w/o newline!
            wait = 0    #wait makes no sense as after upload, the device is still stopped

        if wait:                                        #wait at the end if desired
            sys.stderr.write("Press <ENTER> ...\n")     #display a prompt
            sys.stderr.flush()
            raw_input()                                 #wait for newline
    
        abort_due_to_error = 0
    finally:
        if abort_due_to_error:
            sys.stderr.write("Cleaning up after error...\n")
        jtagobj.reset(1, 1)                             #reset and release target
        if do_close:
            jtagobj.close()                             #Release communication port
            if DEBUG:
                sys.stderr.write("WARNING: JTAG port is left open (--no-close)\n")

if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise                                           #let pass exit() calls
    except KeyboardInterrupt:
        if DEBUG: raise                                 #show full trace in debug mode
        sys.stderr.write("User abort.\n")               #short messy in user mode
        sys.exit(1)                                     #set errorlevel for script usage
    except Exception, msg:                              #every Exception is caught and displayed
        if DEBUG: raise                                 #show full trace in debug mode
        sys.stderr.write("\nAn error occoured:\n%s\n" % msg) #short messy in user mode
        sys.exit(1)                                     #set errorlevel for script usage    
