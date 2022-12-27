# indi_pylibcamera
INDI library (https://indilib.org/) is an open source software to control astronomical equipment.

Raspberry Pi cameras allow the amateur astronomer to make astonishing pictures with small budget. Especially the
Raspberry Pi HQ camera can compete with expensive astro cameras.

This project implements a Raspberry Pi camera driver for INDI. It is based on the new camera framework
"libcamera" (https://github.com/raspberrypi/libcamera) which is already part of many Raspberry Pi operating systems.
It is made to run on a Raspberry Pi Zero wih HQ camera. Ofcourse it will also rn on a more capable Raspberry Pi.

The "indi_pylibcamera" may support all cameras supported by "libcamera". But not all cameras will provide image data
the required formats (raw Bayer or at least RGB). 

## Requirements
- Python 3 and some libraries:
```sudo apt-get install python3 python3-lxml python3-astropy python3-picamera2```
- Libcamera (if not already installed:) `sudo apt-get install libcamera`. You can test libcamera and the support
for your camera with: `libcamera-hello --list-cameras`
- Install INDI core library. If there is no pre-compiled package for your hardware you will need to compile it
by yourself. Instructions can be found here: https://github.com/indilib/indiaspberry. A Raspberry Pi Zero does not
have enough RAM to compile with 4 threads in parallel: you need to do `make -j1` instead of `make -j4`. 
Finally, after installation, you need to have a working INDI server: `indiserver -v indi_simulator_telescope`

## Installation
Currently, the `indi_pylibcamera` driver does not has a setup or installation tool. Just copy the `indidevice.py` and
`indi_pylibcamera.py` in a folder.

## Running
You can start the INDI server with `indiserver -v ./indi_pylibcamer.py`. When the server is running you can connect
to the server from an other computer with an INDI client (for instance KStars/EKOS).

## TODO
- make this an installable Python library
- many many functional improvements and debugging
