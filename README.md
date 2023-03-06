# indi_pylibcamera
INDI library (https://indilib.org/) is an open source software to control astronomical equipment.

Raspberry Pi cameras allow the amateur astronomer to make astonishing pictures with small budget. Especially the
Raspberry Pi HQ camera can compete with expensive astro cameras.

This project implements a Raspberry Pi camera driver for INDI. It is based on the new camera framework
"libcamera" (https://github.com/raspberrypi/libcamera) which is already part of many Raspberry Pi operating systems.

The driver is made and optimized to run on a Raspberry Pi Zero wih HQ camera. Ofcourse it will also run on a more
capable Raspberry Pi.

The "indi_pylibcamera" may support all cameras supported by "libcamera". But not all cameras will provide image data
in the required formats (raw Bayer or at least RGB). So it is not guaranteed that the driver will work with all
cameras you can connect to a Raspberry Pi.

## Requirements
- Python 3 and some libraries:
```sudo apt-get install python3 python3-lxml python3-astropy python3-picamera2```
- Libcamera (if not already installed:) `sudo apt-get install libcamera`. You can test libcamera and the support
for your camera with: `libcamera-hello --list-cameras`
- Install INDI core library. If there is no pre-compiled package for your hardware you will need to compile it
by yourself. Instructions can be found here: https://github.com/indilib/indi. A Raspberry Pi Zero does not
have enough RAM to compile with 4 threads in parallel: you need to do `make -j1` instead of `make -j4`. 
Finally, after installation, you need to have a working INDI server: `indiserver -v indi_simulator_telescope`

## Installation
Currently, the `indi_pylibcamera` driver does not has a setup or installation tool. Just copy the files in a folder.

## Running
You can start the INDI server with `indiserver -v ./indi_pylibcamer.py` after changing to the folder where you stored
`indi_pylibcamera.py`. When the server is running you can connect to the server from another computer with an INDI
client (for instance KStars/EKOS).

## Global Configuration
The driver uses configuration files to set global parameter. If environment variable `INDI_PYLIBCAMERA_CONFIG_PATH`
exists the file `$INDI_PYLIBCAMERA_CONFIG_PATH/indi_pylibcamera.ini` is loaded. Otherwise, it tries to load
`$HOME/.indi_pylibcamera/indi_pylibcamera.ini`.

The configuration file must have the section `[driver]`. The following keys are supported:
- `DeviceName` (string): INDI name of the device. This allows to distinguish indi_pylibcamera devices in your setup.
For instance you can have one Raspberry Pi with HQ camera as main camera for taking photos and a second Raspberry Pi with
a V1 camera for auto guiding.
- `SendTimeStamps` (`yes`, `no`, `on`, `off`, `true`, `false`, `1`, `0`): Add a timestamp to the messages send from
the device to the client. Such timestamps are needed in very seldom cases only, and usually it is okay to set this 
to `no`. If you really need timestamps make sure that the system clock is correct. 

An example for a configuration file can be found in this repository.

## Error when restarting indiserver
When killing the indiserver sometimes the driver process continues to run. You can see this with:

`ps ax | grep indi`


If you get `python3 ././indi_pylibcamera.py` in the output the driver process is still running. In that case you must
kill the driver process manually before you restart the indiserver. Otherwise, you will get a libcamera error 
when connecting to the camera.

## When you need support for a new camera
In case you have trouble, or you see unexpected behavior it will help debugging when you give more information about
your camera. Please run:

`./print_camera_information.py > MyCam.txt`

and send the generated "MyCam.txt" file. 

## Known Limitations
- Snoopying is not supported.
Snooping is an INDI feature which allows a driver to get information from an other driver. For instance the camera
driver can ask the mount driver for the actual position to write these data as metadata in the images. The present
implementation of the indi_pylibcamera driver does not support this.
- The maximum exposure time of the V1 camera is about 1 second. This limitation is caused by libcamera and the kernel
driver. The indi_pylibcamera can not work around this.
- Libcamera reports a higher maximum value for analogue gain than expected. The analogue gain is implemented by hardware
and has therefore well-defined restrictions. It is not clear if the reported higher maximum analogue gain is correct.
