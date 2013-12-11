### README

This repository contains a patched version of python-msp430-tools 0.7 supporting the following msp430 platforms :

* TelosB and clones
* Zolertia Z1
* Arago Systems WisMote

16 bits and 20 bits firmware are supported

### Telos upload

    msp430-bsl-telosb -p <device> -er <firmware.sky>

### Z1 upload

    msp430-bsl-z1 -p <device> -er firmware.z1

### WisMote upload

    msp430-bsl5-uart -p <device> --parity-none --erase=0x8000-0x3FFFF firmware.wismote

(Note: mote must be manually put into bootloader mode prior to the command and reset afterwards)

### Original project 

https://pypi.python.org/pypi/python-msp430-tools
