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

The configuration file must have the section `[driver]`. The most important keys are:
- `DeviceName` (string): INDI name of the device. This allows to distinguish indi_pylibcamera devices in your setup.
For instance you can have one Raspberry Pi with HQ camera as main camera for taking photos and a second Raspberry Pi with
a V1 camera for auto guiding.
- `SendTimeStamps` (`yes`, `no`, `on`, `off`, `true`, `false`, `1`, `0`): Add a timestamp to the messages send from
the device to the client. Such timestamps are needed in very seldom cases only, and usually it is okay to set this 
to `no`. If you really need timestamps make sure that the system clock is correct. 

Some cameras are not fully supported by libcamera and do not provide all needed information. The configuration
file allows to force pixel size and Bayer pattern rotation for such cameras.

An example for a configuration file can be found in this repository.

## Error when restarting indiserver
When killing the indiserver sometimes the driver process continues to run. You can see this with:

`ps ax | grep indi`


If you get `python3 ././indi_pylibcamera.py` in the output the driver process is still running. In that case you must
kill the driver process manually before you restart the indiserver. Otherwise, you will get a libcamera error 
when connecting to the camera.

## Special handling for some cameras
The driver is made as generic as possible by using the camera information provided by libcamera. For instance the raw
modes and frame sizes selectable in the driver are coming from libcamera. Unfortunately some important information
is not provided by libcamera:
* Some cameras have columns or rows filled with 0 or "garbage". These can disturb postprocessing of frames. 
For instance an automated color/brightness adjustment can get confused by these 0s.
* Libcamera creates some raw modes by cropping, and others by binning (or subsampling, this is not sure) of frames.
Binning (and subsampling) influences the viewing angle of the pixel. An INDI CCD driver must provide the used binning
to allow client software to calculate the image viewing angle.

To work around this the driver makes a special handling for the cameras listed below. Due to the removing of 
zero-filled columns/rows the image frame size will be smaller than stated on the raw mode name.

### IMX477 (Raspberry Pi HQ camera)
Libcamera provides 4 raw modes for this camera, some made by binning (or subsampling) and all with 0-filled columns:
* **4056x3040 BGGR 12bit:** provided frame size 4064x3040, no binning, has 8 zero-filled columns on its right, 
final image size is 4056x3040
* **2028x1520 BGGR 12bit:** provided frame size 2032x1520, 2x2 binning, has 6 zero-filled columns on its right 
(8 get removed to avoid 1/2 Bayer pattern), final image size is 2024x1520
* **2028x1080 BGGR 12bit:** provided frame size 2032x1080, 2x2 binning, has 6 zero-filled columns on its right 
(8 get removed to avoid 1/2 Bayer pattern), final image size is 2024x1080
* **1332x990 BGGR 10bit:** provided frame size 1344x990, 2x2 binning, has 12 zero-filled columns on its right,
final image size is 1332x990

### OV5647 (Raspberry Pi V1 camera)
This camera does not add zero-filled columns. But libcamera uses 3 binning modes.
* **2592x1944 GBRG 10bit:** provided frame size 2592x1944, no binning, no garbage columns, final image size is 2592x1944
* **1920x1080 GBRG 10bit:** provided frame size 1920x1080, no binning, no garbage columns, final image size is 1920x1080
* **1296x972 GBRG 10bit:** provided frame size 1296x972, 2x2 binning, no garbage columns, final image size is 1296x972
* **640x480 GBRG 10bit:** provided frame size 640x480, 4x4 binning, no garbage columns, final image size is 640x480

## When you need support for a different camera
In case you have trouble, or you see unexpected behavior it will help debugging when you give more information about
your camera. Please run:

`./print_camera_information.py > MyCam.txt`

and send the generated "MyCam.txt" file.

Furthermore, send one raw image for each available raw mode. Make pictures of a terrestrial object with red, green and
blue areas. Do not change camera position between taking these pictures. It must be possible to measure and compare
object dimensions.

## Known Limitations
- Snoopying is not supported.
Snooping is an INDI feature which allows a driver to get information from an other driver. For instance the camera
driver can ask the mount driver for the actual position to write these data as metadata in the images. The present
implementation of the indi_pylibcamera driver does not support this.
- The maximum exposure time of the V1 camera is about 1 second. This limitation is caused by libcamera and the kernel
driver. The indi_pylibcamera can not work around this.
- Libcamera reports a maximum exposure time for HQ camera of 694.4 seconds. But when trying an exposure of 690 seconds
the capture function already returns after 40 seconds. Likely this maximum exposure time is wrong. A test with 600 seconds
exposure time was successful.
- Libcamera reports a higher maximum value for analogue gain than expected. The analogue gain is implemented by hardware
and has therefore well-defined restrictions. It is not clear if the reported higher maximum analogue gain is correct.
