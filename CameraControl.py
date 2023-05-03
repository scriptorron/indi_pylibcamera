"""
indi_pylibcamera: CameraControl class
"""
import logging
import os.path
import numpy as np
import io
import re
import threading
import datetime
import time

from astropy.io import fits
import astropy.coordinates
import astropy.units

from picamera2 import Picamera2
from libcamera import controls, Rectangle


from indidevice import *


class CameraSettings:
    """exposure settings
    """

    def __init__(
            self,
            ExposureTime=None, AGain=None, DoFastExposure=None, DoRaw=None, ProcSize=None, RawMode=None, Binning=None
    ):
        """constructor

        Args:
            ExposureTime: exposure time in seconds
            AGain: analogue gain
            DoFastExposure: enable fast exposure were next exposure starts immediately after previous
            DoRaw: enable RAW captures
            ProcSize: size (X,Y)) of processed frame
            RawMode: RAW mode to use for RAW capture
        """
        self.ExposureTime = ExposureTime
        self.AGain = AGain
        self.DoFastExposure = DoFastExposure
        self.DoRaw = DoRaw
        self.ProcSize = ProcSize
        self.RawMode = RawMode
        self.Binning = Binning

    def is_RestartNeeded(self, NewCameraSettings):
        """would using NewCameraSettings need a camera restart?
        """
        is_RestartNeeded = (
            self.is_ReconfigurationNeeded(NewCameraSettings)
            or (self.ExposureTime != NewCameraSettings.ExposureTime)
            or (self.AGain != NewCameraSettings.AGain)
        )
        return is_RestartNeeded

    def is_ReconfigurationNeeded(self, NewCameraSettings):
        """would using NewCameraSettings need a camera reconfiguration?
        """
        is_ReconfigurationNeeded = (
            (self.DoFastExposure != NewCameraSettings.DoFastExposure)
            or (self.DoRaw != NewCameraSettings.DoRaw)
            or (self.ProcSize != NewCameraSettings.ProcSize)
            or (self.RawMode != NewCameraSettings.RawMode)
        )
        return is_ReconfigurationNeeded

    def __str__(self):
        return f'CameraSettings ExposureTime={self.ExposureTime}s, AGain={self.AGain}, ' + \
            f'FastExposure={self.DoFastExposure}, DoRaw={self.DoRaw}, ProcSize={self.ProcSize}, RawMode={self.RawMode}'

    def __repr__(self):
        return str(self)


def getLocalFileName(dir: str = ".", prefix: str = "Image_XXX", suffix: str = ".fits"):
    """make image name for local storage

    Valid placeholder in prefix are:
        _XXX: 3 digit image count
        _ISO8601: local time

    Args:
        dir: local directory, will be created if not existing
        prefix: file name prefix with placeholders
        suffix: file name suffix

    Returns:
        path and file name with placeholders dissolved
    """
    os.makedirs(dir, exist_ok=True)
    # replace ISO8601 placeholder in prefix with current time
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    prefix_now = prefix.replace("_ISO8601", f"_{now}")
    # find largest existing image index
    maxidx = 0
    patternstring = prefix_now.replace("_XXX", "_(?P<Idx>\d{3})", 1) + suffix
    patternstring = patternstring.replace(".", "\.")
    pattern = re.compile(patternstring)
    for fn in os.listdir(dir):
        match = pattern.fullmatch(fn)
        if match:
            if "Idx" in match.groupdict():
                idx = int(match.group("Idx"))
                maxidx = max(maxidx, idx)
    #
    maxidx += 1
    filename = prefix_now.replace("_XXX",f"_{maxidx:03d}", 1) + suffix
    return os.path.join(dir, filename)


class CameraControl:
    """camera control and exposure thread
    """

    def __init__(self, parent, config):
        self.parent = parent
        self.config = config
        self.do_CameraAdjustments = config.getboolean("driver", "CameraAdjustments", fallback=True)
        self.IgnoreRawModes = config.getboolean("driver", "IgnoreRawModes", fallback=False)
        # reset states
        self.picam2 = None
        self.present_CameraSettings = CameraSettings()
        self.CamProps = dict()
        self.RawModes = []
        self.min_ExposureTime = None
        self.max_ExposureTime = None
        self.min_AnalogueGain = None
        self.max_AnalogueGain = None
        # exposure loop control
        self.ExposureTime = 0.0
        self.Sig_Do = threading.Event() # do an action
        self.Sig_ActionExpose = threading.Event()  # single or fast exposure
        self.Sig_ActionExit = threading.Event()  # exit exposure loop
        self.Sig_CaptureDone = threading.Event()
        # exposure loop in separate thread
        self.Sig_ActionExit.clear()
        self.Sig_ActionExpose.clear()
        self.Sig_Do.clear()
        self.ExposureThread = threading.Thread(target=self.__ExposureLoop)


    def closeCamera(self):
        """close camera
        """
        logging.info('closing camera')
        # stop exposure loop
        if self.ExposureThread.is_alive():
            self.Sig_ActionExit.set()
            self.Sig_Do.set()
            self.ExposureThread.join()  # wait until exposure loop exits
        # close picam2
        if self.picam2 is not None:
            if self.picam2.started:
                self.picam2.stop_()
        # reset states
        self.picam2 = None
        self.present_CameraSettings = CameraSettings()
        self.CamProps = dict()
        self.RawModes = []
        self.min_ExposureTime = None
        self.max_ExposureTime = None
        self.min_AnalogueGain = None
        self.max_AnalogueGain = None

    def getRawCameraModes(self):
        """get list of usable raw camera modes
        """
        sensor_modes = self.picam2.sensor_modes
        raw_modes = []
        for sensor_mode in sensor_modes:
            # sensor_mode is dict
            # it must have key "format" (usually a packed data format) and can have
            # "unpacked" (unpacked data format)
            if "unpacked" not in sensor_mode.keys():
                sensor_format = sensor_mode["format"]
            else:
                sensor_format = sensor_mode["unpacked"]
            # packed data formats are not supported
            if sensor_format.endswith("_CSI2P"):
                logging.warning(f'raw mode not supported: {sensor_mode}')
                continue
            # only Bayer pattern formats are supported
            if not re.match("S[RGB]{4}[0-9]+", sensor_format):
                logging.warning(f'raw mode not supported: {sensor_mode}')
                continue
            # it seems that self.CamProps["Rotation"] determines the orientation of the Bayer pattern
            if self.CamProps["Rotation"] == 0:
                # at least V1 camera has this
                FITS_format = sensor_format[1:5]
            elif self.CamProps["Rotation"] == 180:
                # at least HQ camera has this
                FITS_format = sensor_format[4:0:-1]
            elif self.CamProps["Rotation"] == 90:
                # don't know if there is such a camera and if the following rotation is right
                FITS_format = "".join([sensor_format[2], sensor_format[4], sensor_format[1], sensor_format[3]])
            elif self.CamProps["Rotation"] in [270, -90]:
                # don't know if there is such a camera and if the following rotation is right
                FITS_format = "".join([sensor_format[3], sensor_format[1], sensor_format[4], sensor_format[2]])
            else:
                logging.warning(f'Sensor rotation {self.CamProps["Rotation"]} not supported!')
                FITS_format = sensor_format[1:5]
            #
            size = sensor_mode["size"]
            # adjustments for cameras:
            #   * 0- or garbage-filled columns
            #   * raw modes with binning or subsampling
            true_size = size
            binning = (1, 1)
            if self.do_CameraAdjustments:
                if self.CamProps["Model"] == 'imx477':
                    if size == (1332, 990):
                        true_size = (1332, 990)
                        binning = (2, 2)
                    elif size == (2028, 1080):
                        true_size = (2024, 1080)
                        binning = (2, 2)
                    elif size == (2028, 1520):
                        true_size = (2024, 1520)
                        binning = (2, 2)
                    elif size == (4056, 3040):
                        true_size = (4056, 3040)
                    else:
                        logging.warning(f'Unsupported frame size {size} for imx477!')
                elif self.CamProps["Model"] == 'ov5647':
                    if size == (640, 480):
                        binning = (4, 4)
                    elif size == (1296, 972):
                        binning = (2, 2)
                    elif size == (1920, 1080):
                        pass
                    elif size == (2592, 1944):
                        pass
                    else:
                        logging.warning(f'Unsupported frame size {size} for ov5647!')
                elif self.CamProps["Model"].startswith("imx708"):
                    if size == (1536, 864):
                        binning = (2, 2)
                    elif size == (2304, 1296):
                        binning = (2, 2)
                    elif size == (4608, 2592):
                        pass
                    else:
                        logging.warning(f'Unsupported frame size {size} for imx708!')
            # add to list of raw formats
            raw_mode = {
                "size": size,
                "true_size": true_size,
                "camera_format": sensor_format,
                "bit_depth": sensor_mode["bit_depth"],
                "FITS_format": FITS_format,
                "binning": binning,
            }
            raw_mode["label"] = f'{raw_mode["size"][0]}x{raw_mode["size"][1]} {raw_mode["FITS_format"]} {raw_mode["bit_depth"]}bit'
            raw_modes.append(raw_mode)
        # sort list of raw formats by size in descending order
        raw_modes.sort(key=lambda k: k["size"][0] * k["size"][1], reverse=True)
        return raw_modes

    def openCamera(self, idx: int):
        """open camera with given index idx
        """
        self.closeCamera()
        logging.info("opening camera")
        self.picam2 = Picamera2(idx)
        # read camera properties
        self.CamProps = self.picam2.camera_properties
        logging.info(f'camera properties: {self.CamProps}')
        # force properties with values from config file
        if "Rotation" not in self.CamProps:
            logging.warning("Camera properties do not have Rotation value. Need to force from config file!")
        self.CamProps["Rotation"] = self.config.getint(
            "driver", "force_Rotation",
            fallback=self.CamProps["Rotation"] if "Rotation" in self.CamProps else 0
        )
        if "UnitCellSize" not in self.CamProps:
            logging.warning("Camera properties do not have UnitCellSize value. Need to force from config file!")
        self.CamProps["UnitCellSize"] = (
            self.config.getint(
                "driver", "force_UnitCellSize_X",
                fallback=self.CamProps["UnitCellSize"][0] if "UnitCellSize" in self.CamProps else 1000
            ),
            self.config.getint(
                "driver", "force_UnitCellSize_Y",
                fallback=self.CamProps["UnitCellSize"][1] if "UnitCellSize" in self.CamProps else 1000
            )
        )
        # newer libcamera version return a libcamera.Rectangle here!
        if type(self.CamProps["PixelArrayActiveAreas"][0]) is Rectangle:
            Rect = self.CamProps["PixelArrayActiveAreas"][0]
            self.CamProps["PixelArrayActiveAreas"] = (Rect.x, Rect.y, Rect.width, Rect.height)
        # raw modes
        self.RawModes = self.getRawCameraModes()
        if self.IgnoreRawModes:
            self.RawModes = []
        # exposure time range
        self.min_ExposureTime, self.max_ExposureTime, default_exp = self.picam2.camera_controls["ExposureTime"]
        self.min_AnalogueGain, self.max_AnalogueGain, default_again = self.picam2.camera_controls["AnalogueGain"]
        # start exposure loop
        self.Sig_ActionExit.clear()
        self.Sig_ActionExpose.clear()
        self.Sig_Do.clear()
        self.ExposureThread.start()

    def getProp(self, name):
        """return camera properties
        """
        return self.CamProps[name]

    def snooped_FitsHeader(self):
        """created FITS header data from snooped data

        Example:
            FOCALLEN=            2.000E+03 / Focal Length (mm)
            APTDIA  =            2.000E+02 / Telescope diameter (mm)
            ROTATANG=            0.000E+00 / Rotator angle in degrees
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
        """
        FitsHeader = []
        if self.parent.config.getboolean("driver", "DoSnooping", fallback=True):
            logging.info("Collecting snooped data.")
            #### FOCALLEN, APTDIA ####
            # values in SCOPE_INFO vector have higher priority than snooped values from mount
            if self.parent.knownVectors["SCOPE_INFO"]["FOCAL_LENGTH"].value > 0:
                logging.debug("Taking focal length and aperture from SCOPE_INFO vector.")
                FocalLength = self.parent.knownVectors["SCOPE_INFO"]["FOCAL_LENGTH"].value
                FitsHeader += [
                    ("FOCALLEN", FocalLength, "Focal Length (mm)"),
                    ("APTDIA", self.parent.knownVectors["SCOPE_INFO"]["APERTURE"].value, "Telescope diameter (mm)"),
                ]
            else:
                TelescopeInfo = self.parent.SnoopingManager.get_Elements("ACTIVE_TELESCOPE", "TELESCOPE_INFO")
                try:
                    FocalLength = float(TelescopeInfo["TELESCOPE_FOCAL_LENGTH"])
                    Aperture = float(TelescopeInfo["TELESCOPE_APERTURE"])
                except (ValueError, KeyError):
                    # not float values or not in data!
                    FocalLength = None  # invalid value for SCALE calculation
                else:
                    FitsHeader += [
                        ("FOCALLEN", FocalLength, "Focal Length (mm)"),
                        ("APTDIA", Aperture, "Telescope diameter (mm)"),
                    ]
            #### SCALE ####
            if FocalLength is not None:
                FitsHeader += [(
                    "SCALE",
                    206.265 * self.getProp("UnitCellSize")[0] * self.present_CameraSettings.Binning[0] / FocalLength,
                    "arcsecs per pixel"
                ), ]
            #### SITELAT, SITELONG ####
            ObsSite = self.parent.SnoopingManager.get_Elements("ACTIVE_TELESCOPE", "GEOGRAPHIC_COORD")
            try:
                Lat = float(ObsSite["LAT"])
                Long = float(ObsSite["LONG"])
                Height = float(ObsSite["ELEV"])
            except (ValueError, KeyError):
                # values are not float or not in data!
                Lat = None
                Long = None
                Height = None
            else:
                FitsHeader += [
                    ("SITELAT", Lat, "Latitude of the imaging site in degrees"),
                    ("SITELONG", Long, "Longitude of the imaging site in degrees"),
                ]
            ####
            Coord = self.parent.SnoopingManager.get_Elements("ACTIVE_TELESCOPE", "EQUATORIAL_COORD")  # J2000 RA DEC
            try:
                J2000RA = float(Coord["RA"])
                J2000DEC = float(Coord["DEC"])
            except (ValueError, KeyError):
                # values are not float or not in data!
                J2000RA = None
                J2000DEC = None
            if J2000RA is not None:
                # got J2000 coordinates from mount!
                FitsHeade += [
                    ("OBJCTRA", J2000.ra.to_string(unit=astropy.units.hour).replace("h", " ").replace("m", " ").replace("s", " "),
                     "Object J2000 RA in Hours"),
                    ("OBJCTDEC", J2000.dec.to_string(unit=astropy.units.deg).replace("d", " ").replace("m", " ").replace("s", " "),
                     "Object J2000 DEC in Degrees"),
                    ("RA", float(J2000.ra.degree), "Object J2000 RA in Degrees"),
                    ("DEC", float(J2000.dec.degree), "Object J2000 DEC in Degrees")
                ]
                # TODO: What about AIRMASS, OBJCTAZ and OBJCTALT?
            else:
                EodCoord = self.parent.SnoopingManager.get_Elements("ACTIVE_TELESCOPE", "EQUATORIAL_EOD_COORD")
                try:
                    RA = float(EodCoord["RA"])
                    DEC = float(EodCoord["DEC"])
                except (ValueError, KeyError):
                    # values are not float or not in data!
                    RA = None
                    DEC = None
                #### AIRMASS, OBJCTAZ, OBJCTALT, OBJCTRA, OBJCTDEC, RA, DEC ####
                if (Lat is not None) and (RA is not None):
                    ObsLoc = astropy.coordinates.EarthLocation(
                        lon=Long * astropy.units.deg, lat=Lat * astropy.units.deg, height=Height * astropy.units.meter
                    )
                    c = astropy.coordinates.SkyCoord(ra=RA * astropy.units.hourangle, dec=DEC * astropy.units.deg)
                    cAltAz = c.transform_to(astropy.coordinates.AltAz(obstime=astropy.time.Time(datetime.datetime.utcnow()), location=ObsLoc))
                    J2000 = cAltAz.transform_to(astropy.coordinates.ICRS())
                    #
                    FitsHeader += [
                        ("AIRMASS", float(cAltAz.secz), "Airmass"),
                        ("OBJCTAZ", float(cAltAz.az/astropy.units.deg), "Azimuth of center of image in Degrees"),
                        ("OBJCTALT", float(cAltAz.alt/astropy.units.deg), "Altitude of center of image in Degrees"),
                        ("OBJCTRA", J2000.ra.to_string(unit=astropy.units.hour).replace("h", " ").replace("m", " ").replace("s", " "), "Object J2000 RA in Hours"),
                        ("OBJCTDEC", J2000.dec.to_string(unit=astropy.units.deg).replace("d", " ").replace("m", " ").replace("s", " "), "Object J2000 DEC in Degrees"),
                        ("RA", float(J2000.ra.degree), "Object J2000 RA in Degrees"),
                        ("DEC", float(J2000.dec.degree), "Object J2000 DEC in Degrees")
                    ]
            #### PIERSIDE ####
            PierSide = self.parent.SnoopingManager.get_Elements("ACTIVE_TELESCOPE", "TELESCOPE_PIER_SIDE")
            try:
                PierWest = PierSide["PIER_WEST"] == "On"
                PierEast = PierSide["PIER_EAST"] == "On"
            except KeyError:
                # value not snooped
                PierWest = False
                PierEast = False
            if PierEast:
                FitsHeader += [("PIERSIDE", "WEST", "West, looking East"),]
            elif PierWest:
                FitsHeader += [("PIERSIDE", "EAST", "East, looking West"),]
            #### EQUINOX and DATE-OBS ####
            FitsHeader += [
                ("EQUINOX", 2000, "Equinox"),
                ("DATE-OBS", datetime.datetime.utcnow().isoformat(timespec="milliseconds"), "UTC start date of observation"),  # FIXME: this is end and not start time!
            ]
            logging.info("Finished collecting snooped data.")
        ####
        return FitsHeader

    def createBayerFits(self, array, metadata):
        """creates Bayer pattern FITS image from raw frame

        Args:
            array: data array
            metadata: metadata
        """
        # type cast and rescale
        bit_depth = self.present_CameraSettings.RawMode["bit_depth"]
        if bit_depth > 8:
            bit_pix = 16
            array = array.view(np.uint16) * (2 ** (bit_pix - bit_depth))
        else:
            bit_pix = 8
            array = array.view(np.uint8) * (2 ** (bit_pix - bit_depth))
        # remove 0- or garbage-filled columns
        true_size = self.present_CameraSettings.RawMode["true_size"]
        array = array[0:true_size[1], 0:true_size[0]]
        # convert to FITS
        hdu = fits.PrimaryHDU(array)
        # avoid access conflicts to knownVectors
        with self.parent.knownVectorsLock:
            # determine frame type
            FrameType = self.parent.knownVectors["CCD_FRAME_TYPE"].get_OnSwitchesLabels()[0]
            # FITS header and metadata
            FitsHeader = [
                ("BZERO", 2 ** (bit_pix - 1), "offset data range"),
                ("BSCALE", 1, "default scaling factor"),
                ("ROWORDER", "TOP-DOWN", "Row order"),
                ("INSTRUME", self.parent.device, "CCD Name"),
                ("TELESCOP", self.parent.knownVectors["ACTIVE_DEVICES"]["ACTIVE_TELESCOPE"].value, "Telescope name"),
                ("OBSERVER", self.parent.knownVectors["FITS_HEADER"]["FITS_OBSERVER"].value, "Observer name"),
                ("OBJECT", self.parent.knownVectors["FITS_HEADER"]["FITS_OBJECT"].value, "Object name"),
                ("EXPTIME", metadata["ExposureTime"]/1e6, "Total Exposure Time (s)"),
                ("CCD-TEMP", metadata.get('SensorTemperature', 0), "CCD Temperature (Celsius)"),
                ("PIXSIZE1", self.getProp("UnitCellSize")[0] / 1e3, "Pixel Size 1 (microns)"),
                ("PIXSIZE2", self.getProp("UnitCellSize")[1] / 1e3, "Pixel Size 2 (microns)"),
                ("XBINNING", self.present_CameraSettings.Binning[0], "Binning factor in width"),
                ("YBINNING", self.present_CameraSettings.Binning[1], "Binning factor in height"),
                ("XPIXSZ", self.getProp("UnitCellSize")[0] / 1e3 * self.present_CameraSettings.Binning[0], "X binned pixel size in microns"),
                ("YPIXSZ", self.getProp("UnitCellSize")[1] / 1e3 * self.present_CameraSettings.Binning[1], "Y binned pixel size in microns"),
                ("FRAME", FrameType, "Frame Type"),
                ("IMAGETYP", FrameType+" Frame", "Frame Type"),
            ] + self.snooped_FitsHeader() + [
                ("XBAYROFF", 0, "X offset of Bayer array"),
                ("YBAYROFF", 0, "Y offset of Bayer array"),
                ("BAYERPAT", self.present_CameraSettings.RawMode["FITS_format"], "Bayer color pattern"),
            ]
        FitsHeader += [("Gain", metadata.get("AnalogueGain", 0.0), "Gain"), ]
        if "SensorBlackLevels" in metadata:
            SensorBlackLevels = metadata["SensorBlackLevels"]
            if len(SensorBlackLevels) == 4:
                # according to pylibcamera2 documentation:
                #   "The black levels of the raw sensor image. This
                #    control appears only in captured image
                #    metadata and is read-only. One value is
                #    reported for each of the four Bayer channels,
                #    scaled up as if the full pixel range were 16 bits
                #    (so 4096 represents a black level of 16 in 10-
                #    bit raw data)."
                # When image data is stored as 16bit it is not needed to scale SensorBlackLevels again.
                # But when we store image with 8bit/pixel we need to divide by 2**8.
                SensorBlackLevelScaling = 2 ** (bit_pix - 16)
                FitsHeader += [
                    ("OFFSET_0", SensorBlackLevels[0] * SensorBlackLevelScaling, "Sensor Black Level 0"),
                    ("OFFSET_1", SensorBlackLevels[1] * SensorBlackLevelScaling, "Sensor Black Level 1"),
                    ("OFFSET_2", SensorBlackLevels[2] * SensorBlackLevelScaling, "Sensor Black Level 2"),
                    ("OFFSET_3", SensorBlackLevels[3] * SensorBlackLevelScaling, "Sensor Black Level 3"),
                ]
        for FHdr in FitsHeader:
            if len(FHdr) > 2:
                hdu.header[FHdr[0]] = (FHdr[1], FHdr[2])
            else:
                hdu.header[FHdr[0]] = FHdr[1]
        hdul = fits.HDUList([hdu])
        return hdul

    def createRgbFits(self, array, metadata):
        """creates RGB FITS image from RGB frame

        Args:
            array: data array
            metadata: metadata
        """
        # convert to FITS
        hdu = fits.PrimaryHDU(array.transpose([2, 0, 1]))
        # avoid access conflicts to knownVectors
        with self.parent.knownVectorsLock:
            # determine frame type
            FrameType = self.parent.knownVectors["CCD_FRAME_TYPE"].get_OnSwitchesLabels()[0]
            # FITS header and metadata
            FitsHeader = [
                # ("CTYPE3", 'RGB'),  # Is that needed to make it a RGB image?
                ("BZERO", 0, "offset data range"),
                ("BSCALE", 1, "default scaling factor"),
                ("DATAMAX", 255),
                ("DATAMIN", 0),
                #("ROWORDER", "TOP-DOWN", "Row Order"),
                ("INSTRUME", self.parent.device, "CCD Name"),
                ("TELESCOP", self.parent.knownVectors["ACTIVE_DEVICES"]["ACTIVE_TELESCOPE"].value, "Telescope name"),
                ("OBSERVER", self.parent.knownVectors["FITS_HEADER"]["FITS_OBSERVER"].value, "Observer name"),
                ("OBJECT", self.parent.knownVectors["FITS_HEADER"]["FITS_OBJECT"].value, "Object name"),
                ("EXPTIME", metadata["ExposureTime"]/1e6, "Total Exposure Time (s)"),
                ("CCD-TEMP", metadata.get('SensorTemperature', 0), "CCD Temperature (Celsius)"),
                ("PIXSIZE1", self.getProp("UnitCellSize")[0] / 1e3, "Pixel Size 1 (microns)"),
                ("PIXSIZE2", self.getProp("UnitCellSize")[1] / 1e3, "Pixel Size 2 (microns)"),
                ("XBINNING", self.present_CameraSettings.Binning[0], "Binning factor in width"),
                ("YBINNING", self.present_CameraSettings.Binning[1], "Binning factor in height"),
                ("XPIXSZ", self.getProp("UnitCellSize")[0] / 1e3 * self.present_CameraSettings.Binning[0], "X binned pixel size in microns"),
                ("YPIXSZ", self.getProp("UnitCellSize")[1] / 1e3 * self.present_CameraSettings.Binning[1], "Y binned pixel size in microns"),
                ("FRAME", FrameType, "Frame Type"),
                ("IMAGETYP", FrameType+" Frame", "Frame Type"),
            ] + self.snooped_FitsHeader() + [
                # more info from camera
                ("Gain", metadata.get("AnalogueGain", 0.0), "Gain"),
            ]
        for FHdr in FitsHeader:
            if len(FHdr) > 2:
                hdu.header[FHdr[0]] = (FHdr[1], FHdr[2])
            else:
                hdu.header[FHdr[0]] = FHdr[1]
        hdul = fits.HDUList([hdu])
        return hdul

    def checkAbort(self):
        """check if client has aborted the exposure

        Reset CCD_FAST_COUNT FRAMES and acknowledge the abort.
        """
        if self.parent.knownVectors["CCD_ABORT_EXPOSURE"]["ABORT"].value == ISwitchState.ON:
            self.parent.setVector("CCD_FAST_COUNT", "FRAMES", value=0, state=IVectorState.OK)
            self.parent.setVector("CCD_ABORT_EXPOSURE", "ABORT", value=ISwitchState.OFF, state=IVectorState.OK)
            return True
        return False

    def __ExposureLoop(self):
        """exposure loop

        Made to run in a separate thread.

        typical communications between client and device:
            start single exposure:
              new CCD_EXPOSURE_VALUE 1
              set CCD_EXPOSURE_VALUE 1 Busy
              set CCD_EXPOSURE_VALUE 0.1 Busy
              set CCD_EXPOSURE_VALUE 0 Busy
              set CCD1 blob Ok
              set CCD_EXPOSURE_VALUE 0 Ok
            start Fast Exposure:
              new CCD_FAST_COUNT 100000
              set CCD_FAST_COUNT 100000 Ok
              new CCD_EXPOSURE_VALUE 1
              set CCD_EXPOSURE_VALUE 1 Busy
              set CCD_EXPOSURE_VALUE 0.1 Busy
              set CCD_EXPOSURE_VALUE 0 Busy
              set CCD_FAST_COUNT 99999 Busy
              set CCD_EXPOSURE_VALUE 0 Busy
              set CCD1 blob
              set CCD_EXPOSURE_VALUE 0 Ok
              set CCD_EXPOSURE_VALUE 0 Busy
              set CCD_FAST_COUNT 99998 Busy
              set CCD_EXPOSURE_VALUE 0 Busy
              set CCD1 blob
            abort:
              new CCD_ABORT_EXPOSURE On
              set CCD_FAST_COUNT 1, Idle
              set CCD_ABORT_EXPOSURE Off, Ok
        """
        while True:
            with self.parent.knownVectorsLock:
                self.checkAbort()
                DoFastExposure = self.parent.knownVectors["CCD_FAST_TOGGLE"]["INDI_ENABLED"].value == ISwitchState.ON
                FastCount_Frames = self.parent.knownVectors["CCD_FAST_COUNT"]["FRAMES"].value
            if not DoFastExposure or (FastCount_Frames < 1):
                # prepare for next exposure
                if FastCount_Frames < 1:
                    self.parent.setVector("CCD_FAST_COUNT", "FRAMES", value=1, state=IVectorState.OK)
                # wait for next action
                self.Sig_Do.wait()
                self.Sig_Do.clear()
            if self.Sig_ActionExpose.is_set():
                self.parent.setVector("CCD_ABORT_EXPOSURE", "ABORT", value=ISwitchState.OFF, state=IVectorState.OK)
                self.Sig_ActionExpose.clear()
            if self.Sig_ActionExit.is_set():
                # exit exposure loop
                self.picam2.stop_()
                return
            # picam2 needs to be open!
            if self.picam2 is None:
                raise RuntimeError("trying to make an exposure without camera opened")
            # get new camera settings for exposure
            has_RawModes = len(self.RawModes) > 0
            with self.parent.knownVectorsLock:
                NewCameraSettings = CameraSettings(
                    ExposureTime=self.ExposureTime,
                    AGain=self.parent.knownVectors["CCD_GAIN"]["GAIN"].value,
                    DoFastExposure=self.parent.knownVectors["CCD_FAST_TOGGLE"]["INDI_ENABLED"].value == ISwitchState.ON,
                    DoRaw=self.parent.knownVectors["FRAME_TYPE"]["FRAMETYPE_RAW"].value == ISwitchState.ON if has_RawModes else False,
                    ProcSize=(int(self.parent.knownVectors["CCD_PROCFRAME"]["WIDTH"].value), int(self.parent.knownVectors["CCD_PROCFRAME"]["HEIGHT"].value)),
                    RawMode=self.parent.knownVectors["RAW_FORMAT"].get_SelectedRawMode() if has_RawModes else None,
                    Binning=(int(self.parent.knownVectors["CCD_BINNING"]["HOR_BIN"].value), int(self.parent.knownVectors["CCD_BINNING"]["VER_BIN"].value))
                )
            logging.info(f'exposure settings: {NewCameraSettings}')
            # need a camera stop/start when something has changed on exposure controls
            IsRestartNeeded = self.present_CameraSettings.is_RestartNeeded(NewCameraSettings)
            if self.picam2.started and IsRestartNeeded:
                logging.info(f'stopping camera for deeper reconfiguration')
                self.picam2.stop_()
            # change of DoFastExposure needs a configuration change
            if self.present_CameraSettings.is_ReconfigurationNeeded(NewCameraSettings):
                logging.info(f'reconfiguring camera')
                # need a new camera configuration
                config = self.picam2.create_still_configuration(
                    queue=NewCameraSettings.DoFastExposure,
                    buffer_count=2  # 2 if NewCameraSettings.DoFastExposure else 1  # need at least 2 buffer for queueing
                )
                if NewCameraSettings.DoRaw:
                    # we do not need the main stream and configure it to smaller size to save memory
                    config["main"]["size"] = (240, 190)
                    # configure raw stream
                    config["raw"] = {"size": NewCameraSettings.RawMode["size"], "format": NewCameraSettings.RawMode["camera_format"]}
                    # libcamera internal binning does not change sensor array mechanical dimensions!
                    #self.parent.setVector("CCD_FRAME", "WIDTH", value=NewCameraSettings.RawMode["size"][0], send=False)
                    #self.parent.setVector("CCD_FRAME", "HEIGHT", value=NewCameraSettings.RawMode["size"][1])
                else:
                    config["main"]["size"] = NewCameraSettings.ProcSize
                    config["main"]["format"] = "BGR888"  # strange: we get RBG when configuring HQ camera as BGR
                    # software image scaling does not change sensor array mechanical dimensions!
                    #self.parent.setVector("CCD_FRAME", "WIDTH", value=NewCameraSettings.ProcSize[0], send=False)
                    #self.parent.setVector("CCD_FRAME", "HEIGHT", value=NewCameraSettings.ProcSize[1])
                # optimize (align) configuration: small changes to some main stream configurations
                # (for instance: size) will fit better to hardware
                self.picam2.align_configuration(config)
                # set still configuration
                self.picam2.configure(config)
            # changing exposure time or analogue gain needs a restart
            if IsRestartNeeded:
                # exposure time and analog gain are controls
                self.picam2.set_controls(
                    {
                        # controls for main frame: disable all regulations
                        "AeEnable": False,  # AEC/AGC algorithm
                        # disable noise reduction in main frame because it eats stars
                        "NoiseReductionMode": controls.draft.NoiseReductionModeEnum.Off,
                        # disable automatic white balance algorithm and set colour gains manually
                        "AwbEnable": False,
                        "ColourGains": (2.0, 2.0),  # to compensate the 2 G pixel in Bayer pattern
                        # controls for raw and main frames
                        "AnalogueGain": NewCameraSettings.AGain,
                        "ExposureTime": int(NewCameraSettings.ExposureTime * 1e6),
                        # exposure time in us; needs to be integer!
                    }
                )
            # start camera if not already running in Fast Exposure mode
            if not self.picam2.started:
                self.picam2.start()
                logging.info(f'camera started')
            # camera runs now with new parameter
            self.present_CameraSettings = NewCameraSettings
            # last chance to exit or abort before doing exposure
            if self.Sig_ActionExit.is_set():
                # exit exposure loop
                self.picam2.stop_()
                return
            with self.parent.knownVectorsLock:
                Abort = self.checkAbort()
            if not Abort:
                # get (non-blocking!) frame and meta data
                self.Sig_CaptureDone.clear()
                ExpectedEndOfExposure = time.time() + self.present_CameraSettings.ExposureTime
                job = self.picam2.capture_arrays(
                    ["raw" if self.present_CameraSettings.DoRaw else "main"],
                    wait=False, signal_function=self.on_CaptureFinished,
                )
                with self.parent.knownVectorsLock:
                    PollingPeriod_s = self.parent.knownVectors["POLLING_PERIOD"]["PERIOD_MS"].value / 1e3
                while ExpectedEndOfExposure - time.time() > PollingPeriod_s:
                    # exposure count down
                    self.parent.setVector(
                        "CCD_EXPOSURE", "CCD_EXPOSURE_VALUE", value=ExpectedEndOfExposure - time.time(),
                        state=IVectorState.BUSY
                    )
                    # allow to close camera
                    if self.Sig_ActionExit.is_set():
                        # exit exposure loop
                        self.picam2.stop_()
                        return
                    # allow to abort exposure
                    with self.parent.knownVectorsLock:
                        Abort = self.checkAbort()
                    if Abort:
                        break
                    # allow exposure to finish earlier than expected (for instance when in fast exposure mode)
                    if self.Sig_CaptureDone.is_set():
                        break
                    time.sleep(PollingPeriod_s)
                # get frame and its metadata
                if not Abort:
                    (array, ), metadata =  self.picam2.wait(job)
                    logging.info("got exposed frame")
                # inform client about progress
                self.parent.setVector("CCD_EXPOSURE", "CCD_EXPOSURE_VALUE", value=0, state=IVectorState.BUSY)
                # at least HQ camera reports CCD temperature in meta data
                self.parent.setVector("CCD_TEMPERATURE", "CCD_TEMPERATURE_VALUE", value=metadata.get('SensorTemperature', 0))
                # last chance to exit or abort before sending blob
                if self.Sig_ActionExit.is_set():
                    # exit exposure loop
                    self.picam2.stop_()
                    return
                with self.parent.knownVectorsLock:
                    Abort = Abort or self.checkAbort()
                    DoFastExposure = self.parent.knownVectors["CCD_FAST_TOGGLE"]["INDI_ENABLED"].value == ISwitchState.ON
                    FastCount_Frames = self.parent.knownVectors["CCD_FAST_COUNT"]["FRAMES"].value
                if not DoFastExposure:
                    # in normal exposure mode the camera needs to be started with exposure command
                    self.picam2.stop_()
                if not Abort:
                    if DoFastExposure:
                        FastCount_Frames -= 1
                        self.parent.setVector("CCD_FAST_COUNT", "FRAMES", value=FastCount_Frames, state=IVectorState.BUSY)
                    # create FITS images
                    if self.present_CameraSettings.DoRaw:
                        hdul = self.createBayerFits(array=array, metadata=metadata)
                    else:
                        hdul = self.createRgbFits(array=array, metadata=metadata)
                    bstream = io.BytesIO()
                    hdul.writeto(bstream)
                    size = bstream.tell()
                    # what to do with image
                    with self.parent.knownVectorsLock:
                        tv = self.parent.knownVectors["UPLOAD_SETTINGS"]
                        upload_dir = tv["UPLOAD_DIR"].value
                        upload_prefix = tv["UPLOAD_PREFIX"].value
                        upload_mode = self.parent.knownVectors["UPLOAD_MODE"].get_OnSwitches()
                    if upload_mode[0] in ["UPLOAD_LOCAL", "UPLOAD_BOTH"]:
                        # requested to save locally
                        local_filename = getLocalFileName(dir=upload_dir, prefix=upload_prefix, suffix=".fits")
                        bstream.seek(0)
                        logging.info(f"saving image to file {local_filename}")
                        with open(local_filename, 'wb') as fh:
                            fh.write(bstream.getbuffer())
                    if upload_mode[0] in ["UPLOAD_CLIENT", "UPLOAD_BOTH"]:
                        # send blob to client
                        bstream.seek(0)
                        # make BLOB
                        logging.info(f"preparing frame as BLOB: {size} bytes")
                        bv = self.parent.knownVectors["CCD1"]
                        compress = self.parent.knownVectors["CCD_COMPRESSION"].get_OnSwitches()[0] == "CCD_COMPRESS"
                        bv["CCD1"].set_data(data=bstream.getbuffer(), format=".fits", compress=compress)
                        logging.info(f"sending BLOB")
                        bv.send_setVector()
                    # tell client that we finished exposure
                    if DoFastExposure:
                        if FastCount_Frames == 0:
                            self.parent.setVector("CCD_FAST_COUNT", "FRAMES", value=0, state=IVectorState.OK)
                            self.parent.setVector("CCD_EXPOSURE", "CCD_EXPOSURE_VALUE", value=0, state=IVectorState.OK)
                    else:
                        self.parent.setVector("CCD_EXPOSURE", "CCD_EXPOSURE_VALUE", value=0, state=IVectorState.OK)

    def on_CaptureFinished(self, Job):
        """callback function for capture done
        """
        self.Sig_CaptureDone.set()

    def startExposure(self, exposuretime):
        """start a single or fast exposure

        Args:
            exposuretime: exposure time (seconds)
        """
        if not self.ExposureThread.is_alive():
            raise RuntimeError("Try ro start exposure without having exposure loop running!")
        self.ExposureTime = exposuretime
        self.Sig_ActionExpose.set()
        self.Sig_ActionExit.clear()
        self.Sig_Do.set()

