#!/usr/bin/env python3

import sys
import os
import os.path
from pathlib import Path
import numpy as np
from astropy.io import fits
import io
import re
import logging
import threading
import datetime

from picamera2 import Picamera2
from libcamera import controls

from configparser import ConfigParser

from indidevice import *


logging.basicConfig(filename=None, level=logging.INFO, format='%(name)s-%(levelname)s- %(message)s')


if "INDI_PYLIBCAMERA_CONFIG_PATH" in os.environ:
    configpath = Path(os.environ["INDI_PYLIBCAMERA_CONFIG_PATH"]) / Path("indi_pylibcamera.ini")
else:
    configpath = Path(os.environ["HOME"]) / Path(".indi_pylibcamera") / Path("indi_pylibcamera.ini")
config = ConfigParser()
config.read(str(configpath))
logging.debug(f"ConfigParser: {configpath}, {config}")

__version__ = "1.2.0"


# INDI vectors with immediate actions

class LoggingVector(ISwitchVector):
    """INDI Switch vector with logging configuration

    Logging verbosity gets changed when client writes this vector.
    """

    def __init__(self, parent):
        self.parent=parent
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="LOGGING_LEVEL",
            elements=[
                ISwitch(name="LOGGING_DEBUG", label="Debug", value=ISwitchState.OFF),
                ISwitch(name="LOGGING_INFO", label="Info", value=ISwitchState.ON),
                ISwitch(name="LOGGING_WARN", label="Warning", value=ISwitchState.OFF),
                ISwitch(name="LOGGING_ERROR", label="Error", value=ISwitchState.OFF),
            ],
            label="Logging", group="Options",
            rule=ISwitchRule.ONEOFMANY,
        )

    def set_byClient(self, values: dict):
        """called when vector gets set by client
        special version for changing logging level

        Args:
            values: dict(propertyName: value) of values to set
        """
        logging.debug(f"logging level action: {values}")
        self.message = self.update_SwitchStates(values=values)
        # send updated property values
        if len(self.message) > 0:
            self.state = IVectorState.ALERT
            self.send_setVector()
            self.message = ""
            return
        selectedLogLevel = self.get_OnSwitches()[0]
        logging.info(f'selected logging level: {selectedLogLevel}')
        if selectedLogLevel == "LOGGING_DEBUG":
            logging.getLogger().setLevel(logging.DEBUG)
        elif selectedLogLevel == "LOGGING_INFO":
            logging.getLogger().setLevel(logging.INFO)
        elif selectedLogLevel == "LOGGING_WARN":
            logging.getLogger().setLevel(logging.WARN)
        else:
            logging.getLogger().setLevel(logging.ERROR)
        self.state = IVectorState.OK
        self.send_setVector()


class ConnectionVector(ISwitchVector):
    """INDI Switch vector with "Connect" and "Disconnect" buttons

    Camera gets connected or disconnected when client writes this vector.
    """

    def __init__(self, parent):
        self.parent=parent
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="CONNECTION",
            elements=[
                ISwitch(name="CONNECT", label="Connect", value=ISwitchState.OFF),
                ISwitch(name="DISCONNECT", label="Disonnect", value=ISwitchState.ON),
            ],
            label="Connection", group="Main Control",
            rule=ISwitchRule.ONEOFMANY,
        )

    def set_byClient(self, values: dict):
        """called when vector gets set by client
        special version for connect/disconnect actions

        Args:
            values: dict(propertyName: value) of values to set
        """
        logging.debug(f"connect/disconnect action: {values}")
        self.message = self.update_SwitchStates(values=values)
        # send updated property values
        if len(self.message) > 0:
            self.state = IVectorState.ALERT
            self.send_setVector()
            self.message = ""
            return
        self.state = IVectorState.BUSY
        self.send_setVector()
        if self.get_OnSwitches()[0] == "CONNECT":
            if self.parent.openCamera():
                self.state = IVectorState.OK
            else:
                self.state = IVectorState.ALERT
        else:
            self.parent.closeCamera()
            self.state = IVectorState.OK
        self.send_setVector()


class ExposureVector(INumberVector):
    """INDI Number vector for exposure time

    Exposure gets started when client writes this vector.
    """
    def __init__(self, parent, min_exp, max_exp):
        self.parent = parent
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="CCD_EXPOSURE",
            elements=[
                INumber(name="CCD_EXPOSURE_VALUE", label="Duration (s)", min=min_exp / 1e6, max=max_exp / 1e6,
                        step=0.001, value=1.0, format="%.3f"),
            ],
            label="Expose", group="Main Control",
        )

    def set_byClient(self, values: dict):
        """called when vector gets set by client
        special version for exposure actions

        Args:
            values: dict(propertyName: value) of values to set
        """
        errmsgs = []
        for propName, value in values.items():
            errmsg = self[propName].set_byClient(value)
            if len(errmsg) > 0:
                errmsgs.append(errmsg)
        # send updated property values
        if len(errmsgs) > 0:
            self.state = IVectorState.ALERT
            self.message = "; ".join(errmsgs)
            self.send_setVector()
            self.message = ""
            return
        else:
            self.state = IVectorState.OK
        self.state = IVectorState.BUSY
        self.send_setVector()
        self.parent.startExposure(exposuretime=self["CCD_EXPOSURE_VALUE"].value)


# camera specific

class CameraSettings:
    """exposure settings
    """

    def __init__(self, ExposureTime=None, AGain=None, DoFastExposure=None, DoRaw=None, ProcSize=None, RawMode=None):
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

    def __init__(self, parent):
        self.parent=parent
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
            # adjustments for cameras with 0- or garbage-filled columns
            if self.CamProps["Model"] == 'imx477':
                if size == (1332, 990):
                    true_size = (1332, 990)
                elif size == (2028, 1080):
                    true_size = (2024, 1080)
                elif size == (2028, 1520):
                    true_size = (2024, 1520)
                elif size == (4056, 3040):
                    true_size = (4056, 3040)
                else:
                    true_size = size
                    logging.warning(f'Unsupported frame size {size} for imx477!')
            else:
                true_size = size
            # add to list of raw formats
            raw_mode = {
                "size": size,
                "true_size": true_size,
                "camera_format": sensor_format,
                "bit_depth": sensor_mode["bit_depth"],
                "FITS_format": FITS_format,
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
        # raw modes
        self.RawModes = self.getRawCameraModes()
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
            FrameType = self.parent.knownVectors["CCD_FRAME_TYPE"].get_OnSwitches()[0]
            # FITS header and metadata
            FitsHeader = [
                ("BZERO", 2 ** (bit_pix - 1), "offset data range"),
                ("BSCALE", 1, "default scaling factor"),
                ("ROWORDER", "TOP-DOWN", "Row order"),
                ("INSTRUME", self.parent.device, "CCD Name"),
                ("TELESCOP", "Unknown", "Telescope name"),  # TODO
                ("OBSERVER", self.parent.knownVectors["FITS_HEADER"]["FITS_OBSERVER"].value, "Observer name"),
                ("OBJECT", self.parent.knownVectors["FITS_HEADER"]["FITS_OBJECT"].value, "Object name"),
                ("EXPTIME", metadata["ExposureTime"]/1e6, "Total Exposure Time (s)"),
                ("CCD-TEMP", metadata.get('SensorTemperature', 0), "CCD Temperature (Celsius)"),
                ("PIXSIZE1", self.getProp("UnitCellSize")[0] / 1e3, "Pixel Size 1 (microns)"),
                ("PIXSIZE2", self.getProp("UnitCellSize")[1] / 1e3, "Pixel Size 2 (microns)"),
                ("XBINNING", 1, "Binning factor in width"),
                ("YBINNING", 1, "Binning factor in height"),
                ("XPIXSZ", self.getProp("UnitCellSize")[0] / 1e3, "X binned pixel size in microns"),
                ("YPIXSZ", self.getProp("UnitCellSize")[1] / 1e3, "Y binned pixel size in microns"),
                ("FRAME", FrameType, "Frame Type"),
                ("IMAGETYP", FrameType+" Frame", "Frame Type"),
                ("XBAYROFF", 0, "X offset of Bayer array"),
                ("YBAYROFF", 0, "Y offset of Bayer array"),
                ("BAYERPAT", self.present_CameraSettings.RawMode["FITS_format"], "Bayer color pattern"),
                #("FOCALLEN", 900, "Focal Length (mm)"),  # TODO
                #("APTDIA", 120, "Telescope diameter (mm)"),  # TODO
                #("DATE-OBS", time.strftime("%Y-%m-%dT%H:%M:%S.000", time.gmtime(FileInfo.get("TimeStamp", 0.0))), "UTC start date of observation"),
                ("Gain", metadata.get("AnalogueGain", 0.0), "Gain"),
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
            FrameType = self.parent.knownVectors["CCD_FRAME_TYPE"].get_OnSwitches()[0]
            # FITS header and metadata
            FitsHeader = [
                # ("CTYPE3", 'RGB'),  # Is that needed to make it a RGB image?
                ("BZERO", 0, "offset data range"),
                ("BSCALE", 1, "default scaling factor"),
                ("DATAMAX", 255),
                ("DATAMIN", 0),
                #("ROWORDER", "TOP-DOWN", "Row Order"),
                ("INSTRUME", self.parent.device, "CCD Name"),
                ("TELESCOP", "Unknown", "Telescope name"),  # TODO
                ("OBSERVER", self.parent.knownVectors["FITS_HEADER"]["FITS_OBSERVER"].value, "Observer name"),
                ("OBJECT", self.parent.knownVectors["FITS_HEADER"]["FITS_OBJECT"].value, "Object name"),
                ("EXPTIME", metadata["ExposureTime"]/1e6, "Total Exposure Time (s)"),
                ("CCD-TEMP", metadata.get('SensorTemperature', 0), "CCD Temperature (Celsius)"),
                ("PIXSIZE1", self.getProp("UnitCellSize")[0] / 1e3, "Pixel Size 1 (microns)"),
                ("PIXSIZE2", self.getProp("UnitCellSize")[1] / 1e3, "Pixel Size 2 (microns)"),
                ("XBINNING", 1, "Binning factor in width"),
                ("YBINNING", 1, "Binning factor in height"),
                ("XPIXSZ", self.getProp("UnitCellSize")[0] / 1e3, "X binned pixel size in microns"),
                ("YPIXSZ", self.getProp("UnitCellSize")[1] / 1e3, "Y binned pixel size in microns"),
                ("FRAME", FrameType, "Frame Type"),
                ("IMAGETYP", FrameType+" Frame", "Frame Type"),
                #("FOCALLEN", 900, "Focal Length (mm)"),  # TODO
                #("APTDIA", 120, "Telescope diameter (mm)"),  # TODO
                #("DATE-OBS", time.strftime("%Y-%m-%dT%H:%M:%S.000", time.gmtime(FileInfo.get("TimeStamp", 0.0))), "UTC start date of observation"),
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
                    RawMode=self.RawModes[self.parent.knownVectors["RAW_FORMAT"].get_OnSwitchesIdxs()[0]] if has_RawModes else None,
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
                # get (blocking!) frame and meta data
                (array, ), metadata = self.picam2.capture_arrays(["raw" if self.present_CameraSettings.DoRaw else "main"])
                logging.info("got exposed frame")
                # inform client about progress
                self.parent.setVector("CCD_EXPOSURE", "CCD_EXPOSURE_VALUE", value=0, state=IVectorState.BUSY)
                # at least HQ camera reports CCD temperature in meta data
                self.parent.setVector("CCD_TEMPERATURE", "CCD_TEMPERATURE_VALUE", value=metadata.get('SensorTemperature', 0))
                # last change to exit or abort before sending blob
                if self.Sig_ActionExit.is_set():
                    # exit exposure loop
                    self.picam2.stop_()
                    return
                with self.parent.knownVectorsLock:
                    Abort = self.checkAbort()
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


# the device driver

class indi_pylibcamera(indidevice):
    """camera driver using libcamera
    """

    def __init__(self, config=None):
        """constructor

        Args:
            config: driver configuration
        """
        super().__init__(device=config.get("driver", "DeviceName", fallback="indi_pylibcamera"))
        self.timestamp = config.getboolean("driver", "SendTimeStamps", fallback=False)
        # camera
        self.CameraThread = CameraControl(parent=self)
        # get connected cameras
        cameras = Picamera2.global_camera_info()
        logging.info(f'found cameras: {cameras}')
        # use Id as unique camera identifier
        self.Cameras = [c["Id"] for c in cameras]
        # INDI vector names only available with connected camera
        self.CameraVectorNames = []
        # INDI general vectors
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="CAMERA_SELECTION",
                elements=[
                    ISwitch(
                        name=f'CAM{i}',
                        value=ISwitchState.ON if i == 0 else ISwitchState.OFF,
                        label=self.Cameras[i]
                    ) for i in range(len(self.Cameras))
                ],
                label="Camera", group="Main Control",
                rule=ISwitchRule.ONEOFMANY,
            )
        )
        self.checkin(
            ConnectionVector(parent=self),
        )
        self.checkin(
            ITextVector(
                device=self.device, timestamp=self.timestamp, name="DRIVER_INFO",
                elements=[
                    # TODO: make driver name editable to allow multiple cameras of same type
                    IText(name="DRIVER_NAME", label="Name", value=self.device),
                    IText(name="DRIVER_EXEC", label="Exec", value=sys.argv[0]),
                    IText(name="DRIVER_VERSION", label="Version", value=__version__),
                    IText(name="DRIVER_INTERFACE", label="Interface", value="2"),  # This is a CCD!
                ],
                label="Driver Info", group="General Info",
                perm=IPermission.RO,
            )
        )
        self.checkin(
            LoggingVector(parent=self),
        )

    def closeCamera(self):
        """close camera and tell client to remove camera vectors from GUI
        """
        self.CameraThread.closeCamera()
        for n in self.CameraVectorNames:
            self.checkout(n)
        self.CameraVectorNames = []

    def openCamera(self):
        """ opens camera, reads camera properties and still configurations, updates INDI properties
        """
        #
        CameraSel = self.knownVectors["CAMERA_SELECTION"].get_OnSwitchesIdxs()
        if len(CameraSel) < 1:
            return False
        CameraIdx = CameraSel[0]
        logging.info(f'connecting to camera {self.Cameras[CameraIdx]}')
        self.closeCamera()
        self.CameraThread.openCamera(CameraIdx)
        # update INDI properties
        self.checkin(
            ITextVector(
                device=self.device, timestamp=self.timestamp, name="CAMERA_INFO",
                elements=[
                    IText(name="CAMERA_MODEL", label="Model", value=self.CameraThread.getProp("Model")),
                    IText(name="CAMERA_PIXELARRAYSIZE", label="Pixel array size", value=str(self.CameraThread.getProp("PixelArraySize"))),
                    IText(name="CAMERA_PIXELARRAYACTIVEAREA", label="Pixel array active area", value=str(self.CameraThread.getProp("PixelArrayActiveAreas"))),
                    IText(name="CAMERA_UNITCELLSIZE", label="Pixel size", value=str(self.CameraThread.getProp("UnitCellSize"))),
                ],
                label="Camera Info", group="General Info",
                state=IVectorState.OK, perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CAMERA_INFO")
        # raw camera modes
        if len(self.CameraThread.RawModes) < 1:
            logging.warning("camera does not has a useful raw mode")
        else:
            # allow to select raw or processed frame
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, name="FRAME_TYPE",
                    elements=[
                        ISwitch(name="FRAMETYPE_RAW", label="Raw", value=ISwitchState.ON),
                        ISwitch(name="FRAMETYPE_PROC", label="Processed", value=ISwitchState.OFF),
                    ],
                    label="Frame type", group="Image Settings",
                    rule=ISwitchRule.ONEOFMANY,
                ),
                send_defVector=True,
            )
            self.CameraVectorNames.append("FRAME_TYPE")
            # raw frame types
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, name="RAW_FORMAT",
                    elements=[
                        ISwitch(name=f'RAWFORMAT{i}', label=rm["label"], value=ISwitchState.ON if i == 0 else ISwitchState.OFF)
                        for i, rm in enumerate(self.CameraThread.RawModes)
                    ],
                    label="Raw format", group="Image Settings",
                    rule=ISwitchRule.ONEOFMANY,
                ),
                send_defVector=True,
            )
            self.CameraVectorNames.append("RAW_FORMAT")
        #
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="CCD_PROCFRAME",
                elements=[
                    INumber(name="WIDTH", label="Width", min=1, max=self.CameraThread.getProp("PixelArraySize")[0],
                            step=0, value=self.CameraThread.getProp("PixelArraySize")[0], format="%4.0f"),
                    INumber(name="HEIGHT", label="Height", min=1, max=self.CameraThread.getProp("PixelArraySize")[1],
                            step=0, value=self.CameraThread.getProp("PixelArraySize")[1], format="%4.0f"),
                ],
                label="Processed frame", group="Image Settings",
                state=IVectorState.IDLE, perm=IPermission.RW,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_PROCFRAME")
        #
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="CCD_INFO",
                elements=[
                    INumber(name="CCD_MAX_X", label="Max. Width", min=1, max=1000000, step=0,
                            value=self.CameraThread.getProp("PixelArraySize")[0], format="%.f"),
                    INumber(name="CCD_MAX_Y", label="Max. Height", min=1, max=1000000, step=0,
                            value=self.CameraThread.getProp("PixelArraySize")[1], format="%.f"),
                    INumber(name="CCD_PIXEL_SIZE", label="Pixel size (um)", min=0, max=1000, step=0,
                            value=max(self.CameraThread.getProp("UnitCellSize")) / 1e3, format="%.2f"),
                    INumber(name="CCD_PIXEL_SIZE_X", label="Pixel size X", min=0, max=1000, step=0,
                            value=self.CameraThread.getProp("UnitCellSize")[0] / 1e3, format="%.2f"),
                    INumber(name="CCD_PIXEL_SIZE_Y", label="Pixel size Y", min=0, max=1000, step=0,
                            value=self.CameraThread.getProp("UnitCellSize")[1] / 1e3, format="%.2f"),
                    INumber(name="CCD_BITSPERPIXEL", label="Bits per pixel", min=0, max=1000, step=0,
                            # using value of first raw mode or 8 if no raw mode available, TODO: is that right?
                            value=8 if len(self.CameraThread.RawModes) < 1 else self.CameraThread.RawModes[0]["bit_depth"], format="%.f"),
                ],
                label="CCD Information", group="Image Info",
                state=IVectorState.IDLE, perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_INFO")
        # This is needed for field solver!
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="CCD_FRAME",
                elements=[
                    INumber(name="X", label="Left", min=0, max=0, step=0, value=0, format="%4.0f"),
                    INumber(name="Y", label="Top", min=0, max=0, step=0, value=0, format="%4.0f"),
                    INumber(name="WIDTH", label="Width", min=1, max=self.CameraThread.getProp("PixelArraySize")[0],
                            step=0, value=self.CameraThread.getProp("PixelArraySize")[0], format="%4.0f"),
                    INumber(name="HEIGHT", label="Height", min=1, max=self.CameraThread.getProp("PixelArraySize")[1],
                            step=0, value=self.CameraThread.getProp("PixelArraySize")[1], format="%4.0f"),
                ],
                label="Frame", group="Image Info",
                state=IVectorState.IDLE, perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_FRAME")
        #
        # self.checkin(
        #     ITextVector(
        #         device=self.device, timestamp=self.timestamp, name="CCD_CFA",
        #         elements=[
        #             IText(name="CFA_OFFSET_X", label="Offset X", value="0"),
        #             IText(name="CFA_OFFSET_Y", label="Offset Y", value="0"),
        #             IText(name="CFA_TYPE", label="Type", value=self.raw_mode["format"][1:].rstrip("0123456789")),
        #         ],
        #         label="Color filter array", group="Image Info",
        #         state=IVectorState.IDLE, perm=IPermission.RO,
        #     ),
        #     send_defVector=True,
        # )
        # self.CameraVectorNames.append("CCD_CFA")
        #
        self.checkin(
            ExposureVector(parent=self, min_exp=self.CameraThread.min_ExposureTime, max_exp=self.CameraThread.max_ExposureTime),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_EXPOSURE")
        #
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="CCD_GAIN",
                elements=[
                    INumber(name="GAIN", label="Analog Gain", min=self.CameraThread.min_AnalogueGain, max=self.CameraThread.max_AnalogueGain, step=0.1,
                            value=self.CameraThread.max_AnalogueGain, format="%.1f"),
                ],
                label="Gain", group="Main Control",
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_GAIN")
        #
        # self.checkin(
        #     INumberVector(
        #         device=self.device, name="CCD_BINNING",
        #         elements=[
        #             INumber(name="HOR_BIN", label="X", min=1, max=1, step=1, value=1, format="%2.0f"),
        #             INumber(name="VER_BIN", label="Y", min=1, max=1, step=1, value=1, format="%2.0f"),
        #         ],
        #         label="Binning", group="Image Settings",
        #         state=IVectorState.IDLE, perm=IPermission.RO,
        #     ),
        #     send_defVector=True,
        # )
        # self.CameraVectorNames.append("CCD_BINNING")
        #
        self.checkin(
            ITextVector(
                device=self.device, timestamp=self.timestamp, name="FITS_HEADER",
                elements=[
                    IText(name="FITS_OBSERVER", label="Observer", value="Unknown"),
                    IText(name="FITS_OBJECT", label="Object", value="Unknown"),
                ],
                label="FITS Header", group="General Info",
                state=IVectorState.IDLE, perm=IPermission.RW,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("FITS_HEADER")
        #
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="CCD_TEMPERATURE",
                elements=[
                    INumber(name="CCD_TEMPERATURE_VALUE", label="Temperature (C)", min=-50, max=50, step=0, value=0, format="%5.2f"),
                ],
                label="Temperature", group="Main Control",
                state=IVectorState.IDLE, perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_TEMPERATURE")
        #
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="CCD_COMPRESSION",
                elements=[
                    ISwitch(name="CCD_COMPRESS", label="Compressed", value=ISwitchState.OFF),
                    ISwitch(name="CCD_RAW", label="Uncompressed", value=ISwitchState.ON),
                ],
                label="Image compression", group="Image Settings",
                rule=ISwitchRule.ONEOFMANY,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_COMPRESSION")
        # the image BLOB
        self.checkin(
            IBlobVector(
                device=self.device, timestamp=self.timestamp, name="CCD1",
                elements=[
                    IBlob(name="CCD1", label="Image"),
                ],
                label="Image Data", group="Image Info",
                state=IVectorState.OK, perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD1")
        #
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="CCD_FRAME_TYPE",
                elements=[
                    ISwitch(name="FRAME_LIGHT", label="Light", value=ISwitchState.ON),
                    ISwitch(name="FRAME_BIAS", label="Bias", value=ISwitchState.OFF),
                    ISwitch(name="FRAME_DARK", label="Dark", value=ISwitchState.OFF),
                    ISwitch(name="FRAME_FLAT", label="Flat", value=ISwitchState.OFF),
                ],
                label="Frame Type", group="Image Settings",
                rule=ISwitchRule.ONEOFMANY,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_FRAME_TYPE")
        #
        # self.checkin(
        #     ISwitchVector(
        #         device=self.device, timestamp=self.timestamp, name="CCD_FRAME_RESET",
        #         elements=[
        #             ISwitch(name="RESET", label="Reset", value=ISwitchState.OFF),
        #         ],
        #         label="Frame Values", group="Image Settings",
        #         rule=ISwitchRule.ONEOFMANY,
        #     ),
        #     send_defVector=True,
        # )
        # self.CameraVectorNames.append("CCD_FRAME_RESET")
        #  TODO: snooping
        # needed for field solver?
        # self.checkin(
        #     ITextVector(
        #         device=self.device, timestamp=self.timestamp, name="ACTIVE_DEVICES",
        #         elements=[
        #             IText(name="ACTIVE_TELESCOPE", label="Telescope", value="Telescope Simulator"),
        #             IText(name="ACTIVE_ROTATOR", label="Rotator", value="Rotator Simulator"),
        #             IText(name="ACTIVE_FOCUSER", label="Focuser", value="Focuser Simulator"),
        #             IText(name="ACTIVE_FILTER", label="Filter", value="CCD Simulator"),
        #             IText(name="ACTIVE_SKYQUALITY", label="Sky Quality", value="SQM"),
        #
        #         ],
        #         label="Snoop devices", group="Options",
        #     ),
        #     send_defVector=True,
        # )
        # self.CameraVectorNames.append("ACTIVE_DEVICES")
        # needed for field solver?
        # self.checkin(
        #     ISwitchVector(
        #         device=self.device, timestamp=self.timestamp, name="TELESCOPE_TYPE",
        #         elements=[
        #             ISwitch(name="TELESCOPE_PRIMARY", label="Primary", value=ISwitchState.ON),
        #             ISwitch(name="TELESCOPE_GUIDE", label="Guide", value=ISwitchState.OFF),
        #         ],
        #         label="Telescope", group="Options",
        #         rule=ISwitchRule.ONEOFMANY,
        #     ),
        #     send_defVector=True,
        # )
        # self.CameraVectorNames.append("TELESCOPE_TYPE")
        #
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="UPLOAD_MODE",
                elements=[
                    ISwitch(name="UPLOAD_CLIENT", label="Client", value=ISwitchState.ON),
                    ISwitch(name="UPLOAD_LOCAL", label="Local", value=ISwitchState.OFF),
                    ISwitch(name="UPLOAD_BOTH", label="Both", value=ISwitchState.OFF),
                ],
                label="Upload", group="Options",
                rule=ISwitchRule.ONEOFMANY,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("UPLOAD_MODE")
        #
        self.checkin(
            ITextVector(
                device=self.device, timestamp=self.timestamp, name="UPLOAD_SETTINGS",
                elements=[
                    IText(name="UPLOAD_DIR", label="Dir", value=str(Path.home())),
                    IText(name="UPLOAD_PREFIX", label="Prefix", value="IMAGE_XXX"),
                ],
                label="Upload Settings", group="Options",
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("UPLOAD_SETTINGS")
        #
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="CCD_FAST_TOGGLE",
                elements=[
                    ISwitch(name="INDI_ENABLED", label="Enabled", value=ISwitchState.OFF),
                    ISwitch(name="INDI_DISABLED", label="Disabled", value=ISwitchState.ON),
                ],
                label="Fast Exposure", group="Main Control",
                rule=ISwitchRule.ONEOFMANY,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_FAST_TOGGLE")
        # need also CCD_FAST_COUNT for fast exposure
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="CCD_FAST_COUNT",
                elements=[
                    INumber(name="FRAMES", label="Frames", min=0, max=100000, step=1, value=1, format="%.f"),
                ],
                label="Fast Count", group="Main Control",
                state=IVectorState.IDLE,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_FAST_COUNT")
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="CCD_ABORT_EXPOSURE",
                elements=[
                    ISwitch(name="ABORT", label="Abort", value=ISwitchState.OFF),
                ],
                label="Expose Abort", group="Main Control",
                rule=ISwitchRule.ATMOST1,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_ABORT_EXPOSURE")
        # finish
        return True

    def setVector(self, name: str, element: str, value = None, state: IVectorState = None, send: bool = True):
        """update vector value and/or state

        Args:
            name: vector name
            element: element name in vector
            value: new element value or None if unchanged
            state: vector state or None if unchanged
            send: send update to server
        """
        v = self.knownVectors[name]
        if value is not None:
            v[element] = value
        if state is not None:
            v.state = state
        if send:
            v.send_setVector()

    def startExposure(self, exposuretime):
        """start single or fast exposure

        Args:
            exposuretime: exposure time (seconds)
        """
        self.CameraThread.startExposure(exposuretime)


# main entry point

if __name__ == "__main__":
    device = indi_pylibcamera(config=config)
    device.run()
