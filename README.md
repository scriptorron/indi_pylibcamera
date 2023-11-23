# indi_pylibcamera
This project implements a Raspberry Pi camera driver for INDI (https://indilib.org/). 

Raspberry Pi cameras allow the amateur astronomer to make astonishing pictures with small budget. Especially the
Raspberry Pi HQ camera can compete with expensive astro cameras.

The driver is based on the new camera framework "libcamera" (https://github.com/raspberrypi/libcamera) which is
already part of many Raspberry Pi operating systems. It is made and optimized to run on a Raspberry Pi Zero with
HQ camera connected. Of course, it will also run on a more capable Raspberry Pi.

The "indi_pylibcamera" may support all cameras supported by "libcamera". But not all cameras will provide image data
in the required formats (raw Bayer or at least RGB). So it is not guaranteed that the driver will work with all
cameras you can connect to a Raspberry Pi.

The 'indi_pylibcamera' is one layer in a stack of software:
```
    INDI client (for instance KStars, PHD2, CCDciel, ...)
        --> INDI server
            --> indi_pylibcamera
                --> picamera2
                    --> libcamera library
                        --> kernel driver
```
It can not work when the versions of `libcamera` and `picamera2` are too old (both are in a dynamic development).
And it can not work when the libcamera-tools (like `libcamera-hello` and `libcamera-still`) have issues with your
camera.

## Requirements and installation
Some packages need to be installed with apt-get:
- `libcamera` (if not already installed). You can test libcamera and the support
for your camera with: 
  ```commandline
  libcamera-hello --list-cameras
  ```
  You must be able to make RAW pictures in all modes. For instance `libcamera-hello` shows for the HQ camera:
  ```
  0 : imx477 [4056x3040] (/base/soc/i2c0mux/i2c@1/imx477@1a)
    Modes: 'SRGGB10_CSI2P' : 1332x990 [120.05 fps - (696, 528)/2664x1980 crop]
           'SRGGB12_CSI2P' : 2028x1080 [50.03 fps - (0, 440)/4056x2160 crop]
                             2028x1520 [40.01 fps - (0, 0)/4056x3040 crop]
                             4056x3040 [10.00 fps - (0, 0)/4056x3040 crop]
  ```
  and you must be able to run these commands without errors:
  ```commandline
  libcamera-still -r --mode 1332:990 --shutter 100000 --gain 1 --awbgains 1,1 --immediate -o test.jpg
  libcamera-still -r --mode 2028:1080 --shutter 100000 --gain 1 --awbgains 1,1 --immediate -o test.jpg
  libcamera-still -r --mode 2028:1520 --shutter 100000 --gain 1 --awbgains 1,1 --immediate -o test.jpg
  libcamera-still -r --mode 4056:3040 --shutter 100000 --gain 1 --awbgains 1,1 --immediate -o test.jpg
  ```
  Something with your libcamera or kernel driver installation will be wrong if this does not work.
- Install INDI core library. If there is no pre-compiled package for your hardware you will need to compile it
by yourself. Instructions can be found here: https://github.com/indilib/indi. A Raspberry Pi Zero does not
have enough RAM to compile with 4 threads in parallel: you need to do `make -j1` instead of `make -j4`. 
Finally, after installation, you need to have a working INDI server: `indiserver -v indi_simulator_telescope`
- The Python packages `picamera2`, `numpy`, `lxml` and `astropy`. Theoretically these packages can be installed with `pip`. 
But at least the version of `picamera2` must fit to the `libcamera` you installed with `apt-get`. Therefore it is
safer to install these Python packages with `apt-get` too. 

The command line to install all is:
```commandline
sudo apt-get install libcamera-apps indi-bin python3-picamera2 python3-lxml python3-astropy python3-numpy
```

The `indi_pylibcamera` driver package is available on PyPi. Please install with:
```commandline
sudo pip3 install indi_pylibcamera
sudo indi_pylibcamera_postinstall
```

The `indi_pylibcamera_postinstall` script creates in `/usr/share/indi` a symbolic link to the driver XML. That makes
the driver available in the KStars/EKOS profile editor in "CCD"->"OTHERS". Not all versions ov KStars/ECOS support this
(for instance it works with KStars 3.6.5 but not with KStars 3.4.3).

## Uninstall
For uninstalling the driver do:
```commandline
sudo rm -f /usr/share/indi/indi_pylibcamera.xml
sudo pip3 uninstall indi_pylibcamera
```

## Running
You can start the INDI server with `indiserver -v indi_pylibcamera`. When the server is running you can connect to
the server from another computer with an INDI client (for instance KStars/EKOS). The camera name is the one
you configure in `indi_pylibcamera.ini`.

## Global Configuration
The driver uses a hierarchy of configuration files to set global parameter. These configuration files are loaded in the
following order:
- `indi_pylibcamera.ini` in the program installation directory (typically in `/usr/lib/python*/site_packages`)
- `$INDI_PYLIBCAMERA_CONFIG_PATH/indi_pylibcamera.ini`
- `$HOME/.indi_pylibcamera/indi_pylibcamera.ini`
- `./.indi_pylibcamera/indi_pylibcamera.ini`

The configuration file must have the section `[driver]`. The most important keys are:
- `DeviceName` (string): INDI name of the device. This allows to distinguish indi_pylibcamera devices in your setup.
For instance you can have one Raspberry Pi with HQ camera as main camera for taking photos and a second Raspberry Pi with
a V1 camera for auto guiding.
- `SendTimeStamps` (`yes`, `no`, `on`, `off`, `true`, `false`, `1`, `0`): Add a timestamp to the messages send from
the device to the client. Such timestamps are needed in very seldom cases only, and usually it is okay to set this 
to `no`. If you really need timestamps make sure that the system clock is correct. 
- `force_UnitCellSize_X`, `force_UnitCellSize_Y` and `force_Rotation`: Some cameras are not fully supported by
libcamera and do not provide all needed information. The configuration file allows to force pixel size and Bayer
pattern rotation for such cameras.
- `LoggingLevel`: The driver has buttons to set the logging level. But sometimes you need a higher logging level right
at the beginning of the driver initialization. This can be done here in the INI file.
- `DoSnooping`: The INDI protocol allows a driver to ask other drivers for information. This is called "snooping". The
indi_pylibcamera driver uses this feature to get observer location, telescope information and telescope direction
from the mount driver. It writes these information as metadata in the FITS images. This function got newly implemented
and may make trouble in some setups. With the `DoSnooping` you can disable this function.
- `force_Restart` (`yes`, `no`, `auto`): Some cameras crash after the first exposure. Restarting the camera before
every frame exposure can solve this issue. Valid values of this switch are:
  * `no`: Do not restart if not needed to reconfigure camera.
  * `yes`: Always restart. Can lead to longer time between frames.
  * `auto`: Automatically choose based on list of known critical cameras. 

  Default (if not otherwise set in INI file) is `auto`.


There are more settings, mostly to support debugging.

An example for a configuration file can be found in this repository.

## Saving a Configuration
The driver allows you to save up to 6 different configurations. The "Options" tab has 3 controls for that:
- You to select which of the 6 configurations you want to save, load or delete with control "Configs".
- "Config Name" allows you to give the configuration a name. This is optional. It only helps you to remember
what this configuration is made for.
- The buttons in "Configuration" trigger the actions:
  - "Load" loads and applies the configuration.
  - "Save" stores the configuration.
  - "Default" restores the driver defaults. It does not overwrite the stored configuration.
  - "Purge" removes the stored configuration.

When you try to load a non-existing configuration no settings will be changed.

Many clients load "Config #1" automatically. If you do not want this you must purge "Config #1".

Not all driver settings will be stored. For instance all settings which trigger an action (like "Connect", 
"Expose" and "Abort") will not be stored and load. Also, "Scope Location" (your place on earth), "Eq. Coordinates"
(the scope pointing coordinates) and "Pier Side" will not be stored and loaded because these will typically set 
by snooping the mount driver. Telescope focal length and aperture are stored but get overwritten immediately by 
client (EKOS) when snooping. Generally all settings coming from client (EKOS) will overwrite settings you loaded
previously from a configuration file.

To save and load configurations you must be connected to a camera. The configuration will only be valid
for this particular camera. It is not recommended to load a configuration which was saved for a different type
of camera.

Configurations get stored in `~/.indi_pylibcamera/CONFIG*.json`.

## Error when restarting indiserver
When killing the indiserver sometimes the driver process continues to run. You can see this with:

`ps ax | grep indi`


If you get a line containing `python3` and `indi_pylibcamera` in the output the driver process is still running. In that case you must
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

Maximum exposure time is > 5 minutes.

### OV5647 (Raspberry Pi V1 camera)
This camera does not add zero-filled columns. But libcamera uses 3 binning modes. Maximum exposure time is 1 sec.
* **2592x1944 GBRG 10bit:** provided frame size 2592x1944, no binning, no garbage columns, final image size is 2592x1944
* **1920x1080 GBRG 10bit:** provided frame size 1920x1080, no binning, no garbage columns, final image size is 1920x1080
* **1296x972 GBRG 10bit:** provided frame size 1296x972, 2x2 binning, no garbage columns, final image size is 1296x972
* **640x480 GBRG 10bit:** provided frame size 640x480, 4x4 binning, no garbage columns, final image size is 640x480

### IMX708 (Raspberry Pi Module 3 camera)
This camera has auto-focus capabilities which are not supported by this driver. Maximum exposure time is 1.7 sec.
* **4608x2592 BGGR 10bit:** provided frame size 4608x2592, no binning, no garbage columns, final image size is 4608x2592
* **2304x1296 BGGR 10bit:** provided frame size 2304x1296, 2x2 binning, no garbage columns, final image size is 2304x1296
* **1536x864 BGGR 10bit:** provided frame size 1536x864, 2x2 binning, no garbage columns, final image size is 1536x864

## When you need support for a different camera
There are many cameras you can connect to a Raspberry Pi. We can not test the driver with all of them. But we can try 
to support. For that we will need more information about your camera. Please run:

```commandline
libcamera-hello --list-cameras

indi_pylibcamera_print_camera_information > MyCam.txt
```

and send the generated "MyCam.txt" file.

Furthermore, send one raw image for each available raw mode. Make pictures of a terrestrial object with red, green and
blue areas. Do not change camera position between taking these pictures. It must be possible to measure and compare
object dimensions.

## When you see strange behavior or errors
In case you have trouble or you see unexpected behavior it will help debugging when you give more information about
your system and camera. Please run:
```commandline
cat /etc/os-release

uname -a

apt list --installed | grep numpy

apt list --installed | grep astropy

apt list --installed | grep libcamera

apt list --installed | grep picamera

libcamera-hello --list-cameras

indi_pylibcamera_print_camera_information
```

and send the outputs in your issue report.

Please also try to get raw images with `libcamera-still`:
```commandline
libcamera-still -r -o test.jpg --shutter 1000000 --gain 1 --awbgains 1,1 --immediate
```

## Snooping
The `indi_pylibcamera` driver uses snooping to get information from the mount driver. This information is used to add
more metadata to the FITS images, similar to this:
```
FOCALLEN=            2.000E+03 / Focal Length (mm)
APTDIA  =            2.000E+02 / Telescope diameter (mm)
SCALE   =         1.598825E-01 / arcsecs per pixel
SITELAT =         5.105000E+01 / Latitude of the imaging site in degrees
SITELONG=         1.375000E+01 / Longitude of the imaging site in degrees
AIRMASS =         1.643007E+00 / Airmass
OBJCTAZ =         1.121091E+02 / Azimuth of center of image in Degrees
OBJCTALT=         3.744145E+01 / Altitude of center of image in Degrees
OBJCTRA = ' 4 36 07.37'        / Object J2000 RA in Hours
OBJCTDEC= '16 30 26.02'        / Object J2000 DEC in Degrees
RA      =         6.903072E+01 / Object J2000 RA in Degrees
DEC     =         1.650723E+01 / Object J2000 DEC in Degrees
PIERSIDE= 'WEST    '           / West, looking East
EQUINOX =                 2000 / Equinox
DATE-OBS= '2023-04-05T11:27:53.655' / UTC start date of observation
```
Snooping is configured on the "Snooping" tab of the driver. Here you can set the mount driver (ECOS should do this for
you automatically). Furthermore you can set which lenses your camera uses (main or guide scope). When snooping is
enabled the telescope location, sky coordinates and pier side should update automatically. With the buttons 
"Do snooping" you can stop the updates. If you get trouble you can disable snooping in the INI file right from the
driver start.

A correct system time on you Raspberry Pi is absolutely needed for the calculation of the metadata. The Raspberry Pi
does not have a battery powered realtime clock. It adjusts its system time from a time-server in the internet. If your
Pi does not have internet access you will need to take care for setting the date and time. For instance, you can 
install a realtime clock or a GPS hardware. You can also copy date and time from one Linux computer (or Raspberry Pi)
to another with:
```commandline
ssh -t YourUserName@YourPiName sudo date --set=`date -Iseconds`
```
The driver uses "astropy" (https://www.astropy.org/) for coordinate transformations. When processing of the first image
you make the "astropy" library needs a few seconds for initialization. This will not happen anymore for the next images.

## Camera controls
The driver tab "Camera controls" allows you to change low level camera settings. For instance you can enable the
automatic exposure control (AeEnable) and the automatic white balance (AwbEnable) to get processed pictures in good
light conditions.
A detailed description of the camera controls can be found in appendix C of the `picamera2` manual
(https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).

Please be aware that most of the camera controls affect processed (RGB) pictures only. Do not use automatic exposure
control or AWB when you plan to stack images.

## Client Software

### KStars/EKOS
The driver is developed and tested with KStars/EKOS.

### CCDciel
Since version 2.3.0 the driver works with CCDciel. But you need to stop the preview loop before you start image
captures, otherwise CCDciel will tell you that it can not start exposures. The same happens when using
indi_simulator_ccd as camera driver. 

## Known Limitations
- The maximum exposure time of the V1 camera is about 1 second. This limitation is caused by libcamera and the kernel
driver. The indi_pylibcamera can not work around this.
- Libcamera reports a maximum exposure time for HQ camera of 694.4 seconds. But when trying an exposure of 690 seconds
the capture function already returns after 40 seconds. Likely this maximum exposure time is wrong. A test with 600 seconds
exposure time was successful.
- Libcamera reports a higher maximum value for analogue gain than expected. The analogue gain is implemented by hardware
and has therefore well-defined restrictions. It is not clear if the reported higher maximum analogue gain is correct.

## Credits
Many thanks to all who helped to improve this software. Contributions came from:
- Simon Šander
- Aaron W Morris
- Caden Gobat
- anjok

I hope I did not forget someone. If so please do not be angry and tell me.
