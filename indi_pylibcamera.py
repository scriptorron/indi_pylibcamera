#!/usr/bin/env python3

import sys
from pathlib import Path
import numpy as np
from astropy.io import fits
import io
import re
import logging

from picamera2 import Picamera2
from libcamera import controls

from configparser import ConfigParser
import os

from indidevice import *



if "PYINDI_CONFIG_PATH" in os.environ:
    configpath = Path(os.environ["PYINDI_CONFIG_PATH"]) /\
            Path("pyindi.ini")
else:
    configpath = Path(os.environ["HOME"]) / Path(".pyindi") /\
            Path("pyindi.ini")


logging.basicConfig(filename=None, level=logging.INFO, format='%(name)s-%(levelname)s- %(message)s')


config = ConfigParser()
config.read(str(configpath))

__version__ = "1.0.0"


class LoggingVector(ISwitchVector):
    def __init__(self, parent):
        self.parent=parent
        super().__init__(
            device=self.parent.device, name="LOGGING_LEVEL",
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
        logging.error(f'selected logging level: {selectedLogLevel}')
        if selectedLogLevel == "LOGGING_DEBUG":
            logging.getLogger().setLevel(logging.DEBUG)
        elif selectedLogLevel == "LOGGING_INFO":
            logging.getLogger().setLevel(logging.INFO)
        elif selectedLogLevel == "LOGGING_WARN":
            logging.getLogger().setLevel(logging.WARN)
        else:
            logging.getLogger().setLevel(logging.ERROR)
            logging.error(f'logging level geaendert')
        self.state = IVectorState.OK
        self.send_setVector()


class ConnectionVector(ISwitchVector):
    def __init__(self, parent):
        self.parent=parent
        super().__init__(
            device=self.parent.device, name="CONNECTION",
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
            if self.parent.open_Camera():
                self.state = IVectorState.OK
            else:
                self.state = IVectorState.ALERT
        else:
            self.parent.close_Camera()
            self.state = IVectorState.OK
        self.send_setVector()


class ExposureVector(INumberVector):
    def __init__(self, parent, min_exp, max_exp, default_exp):
        self.parent = parent
        super().__init__(
            device=self.parent.device, name="CCD_EXPOSURE",
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
        if self.parent.do_Exposure():
            self.state = IVectorState.OK
        else:
            self.state = IVectorState.ALERT
        self["CCD_EXPOSURE_VALUE"] = 0
        self.send_setVector()



class CameraSettings:
    def __init__(self, ExposureTime=None, AGain=None, DoFastExposure=None, DoRaw=None, ProcSize=None, RawMode=None):
        self.ExposureTime = ExposureTime
        self.AGain = AGain
        self.DoFastExposure = DoFastExposure
        self.DoRaw = DoRaw
        self.ProcSize = ProcSize
        self.RawMode = RawMode

    def is_RestartNeeded(self, NewCameraSettings):
        is_RestartNeeded = (
            (self.DoFastExposure != NewCameraSettings.DoFastExposure)
            or (self.ExposureTime != NewCameraSettings.ExposureTime)
            or (self.AGain != NewCameraSettings.AGain)
            or (self.DoRaw != NewCameraSettings.DoRaw)
            or (self.ProcSize != NewCameraSettings.ProcSize)
            or (self.RawMode != NewCameraSettings.RawMode)
        )
        return is_RestartNeeded

    def is_ReconfigurationNeeded(self, NewCameraSettings):
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





class indi_pylibcamera(indidevice):
    """
    Python driver for libcamera
    """

    def __init__(self, config=None, device="indi_pylibcamera"):
        super().__init__(device=device)
        # get connected cameras
        cameras = Picamera2.global_camera_info()
        logging.info(f'found cameras: {cameras}')
        # use Id as unique camera identifier
        self.Cameras = [c["Id"] for c in cameras]
        # libcamera
        self.picam2 = None
        self.present_CameraSettings = CameraSettings()
        self.CamProps = dict()
        self.RawModes = []
        # INDI vector names only available with connected camera
        self.CameraVectorNames = []
        # INDI general vectors
        self.checkin(
            ISwitchVector(
                device=self.device, name="CAMERA_SELECTION",
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
                device=self.device, name="DRIVER_INFO",
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


    def close_Camera(self):
        if self.picam2 is not None:
            logging.info('disconnecting camera')
            for n in self.CameraVectorNames:
                self.checkout(n)
            self.CameraVectorNames = []
            if self.picam2.started:
                self.picam2.stop()
            self.picam2 = None
        self.present_CameraSettings = CameraSettings()
        self.CamProps = dict()
        self.RawModes = []


    def get_RawCameraModes(self):
        """ get list of useful raw camera modes
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
                FITS_format = sensor_format[4:0:-1]
            elif self.CamProps["Rotation"] == 180:
                # at least HQ camera has this
                FITS_format = sensor_format[1:5]
            elif self.CamProps["Rotation"] == 90:
                # don't know if there is such a camera and if the following rotation is right
                FITS_format = "".join([sensor_format[2], sensor_format[4], sensor_format[1], sensor_format[3]])
            elif self.CamProps["Rotation"] in [270, -90]:
                # don't know if there is such a camera and if the following rotation is right
                FITS_format = "".join([sensor_format[3], sensor_format[1], sensor_format[4], sensor_format[2]])
            else:
                logging.warning(f'Sensor rotation {self.CamProps["Rotation"]} not supported!')
                FITS_format = sensor_format[4:0:-1]
            # add to list of raw formats
            raw_mode = {
                "size": sensor_mode["size"],
                "camera_format": sensor_format,
                "bit_depth": sensor_mode["bit_depth"],
                "FITS_format": FITS_format,
            }
            raw_mode["label"] = f'{raw_mode["size"][0]}x{raw_mode["size"][1]} {raw_mode["FITS_format"]} {raw_mode["bit_depth"]}bit'
            raw_modes.append(raw_mode)
        # sort list of raw formats by size in descending order
        raw_modes.sort(key=lambda k: k["size"][0] * k["size"][1], reverse=True)
        return raw_modes


    def open_Camera(self):
        """ opens camera, reads camera properties and still configurations, updates INDI properties
        """
        #
        CameraSel = self.knownVectors["CAMERA_SELECTION"].get_OnSwitchesIdxs()
        if len(CameraSel) < 1:
            return False
        CameraIdx = CameraSel[0]
        self.close_Camera()
        logging.info(f'connecting to camera {self.Cameras[CameraIdx]}')
        self.picam2 = Picamera2(CameraIdx)
        # read camera properties
        self.CamProps = self.picam2.camera_properties
        logging.info(f'camera properties: {self.CamProps}')
        # update INDI properties
        self.CameraVectorNames = []
        self.checkin(
            ITextVector(
                device=self.device, name="CAMERA_INFO",
                elements=[
                    IText(name="CAMERA_MODEL", label="Model", value=self.CamProps["Model"]),
                    IText(name="CAMERA_PIXELARRAYSIZE", label="Pixel array size", value=str(self.CamProps["PixelArraySize"])),
                    IText(name="CAMERA_PIXELARRAYACTIVEAREA", label="Pixel array active area", value=str(self.CamProps["PixelArrayActiveAreas"])),
                    IText(name="CAMERA_UNITCELLSIZE", label="Pixel size", value=str(self.CamProps["UnitCellSize"])),
                ],
                label="Camera Info", group="General Info",
                state=IVectorState.OK, perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CAMERA_INFO")
        # raw camera modes
        self.RawModes = self.get_RawCameraModes()
        if len(self.RawModes) < 1:
            logging.warning("camera does not has a useful raw mode")
        else:
            # allow to select raw or processed frame
            self.checkin(
                ISwitchVector(
                    device=self.device, name="FRAME_TYPE",
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
                    device=self.device, name="RAW_FORMAT",
                    elements=[
                        ISwitch(name=f'RAWFORMAT{i}', label=rm["label"], value=ISwitchState.ON if i == 0 else ISwitchState.OFF)
                        for i, rm in enumerate(self.RawModes)
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
                device=self.device, name="CCD_FRAME",
                elements=[
                    #INumber(name="X", label="Left", min=0, max=0, step=0, value=0, format="%4.0f"),
                    #INumber(name="Y", label="Top", min=0, max=0, step=0, value=0, format="%4.0f"),
                    INumber(name="WIDTH", label="Width", min=1, max=self.CamProps["PixelArraySize"][0],
                            step=0, value=self.CamProps["PixelArraySize"][0], format="%4.0f"),
                    INumber(name="HEIGHT", label="Height", min=1, max=self.CamProps["PixelArraySize"][1],
                            step=0, value=self.CamProps["PixelArraySize"][1], format="%4.0f"),
                ],
                label="Processed frame", group="Image Settings",
                state=IVectorState.IDLE, perm=IPermission.RW,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_FRAME")
        #
        self.checkin(
            INumberVector(
                device=self.device, name="CCD_INFO",
                elements=[
                    INumber(name="CCD_MAX_X", label="Max. Width", min=1, max=1000000, step=0,
                            value=self.CamProps["PixelArraySize"][0], format="%.f"),
                    INumber(name="CCD_MAX_Y", label="Max. Height", min=1, max=1000000, step=0,
                            value=self.CamProps["PixelArraySize"][1], format="%.f"),
                    INumber(name="CCD_PIXEL_SIZE", label="Pixel size (um)", min=0, max=1000, step=0,
                            value=max(self.CamProps["UnitCellSize"]) / 1e3, format="%.2f"),
                    INumber(name="CCD_PIXEL_SIZE_X", label="Pixel size X", min=0, max=1000, step=0,
                            value=self.CamProps["UnitCellSize"][0] / 1e3, format="%.2f"),
                    INumber(name="CCD_PIXEL_SIZE_Y", label="Pixel size Y", min=0, max=1000, step=0,
                            value=self.CamProps["UnitCellSize"][1] / 1e3, format="%.2f"),
                    INumber(name="CCD_BITSPERPIXEL", label="Bits per pixel", min=0, max=1000, step=0,
                            # using value of first raw mode or 8 if no raw mode available, TODO: is that right?
                            value=8 if len(self.RawModes) < 1 else self.RawModes[0]["bit_depth"], format="%.f"),
                ],
                label="CCD Information", group="Image Info",
                state=IVectorState.IDLE, perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_INFO")
        #
        # self.checkin(
        #     ITextVector(
        #         device=self.device, name="CCD_CFA",
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
        min_exp, max_exp, default_exp = self.picam2.camera_controls["ExposureTime"]
        self.checkin(
            ExposureVector(parent=self, min_exp=min_exp, max_exp=max_exp, default_exp=default_exp),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_EXPOSURE")
        #
        min_again, max_again, default_again = self.picam2.camera_controls["AnalogueGain"]
        self.checkin(
            INumberVector(
                device=self.device, name="CCD_GAIN",
                elements=[
                    INumber(name="GAIN", label="Analog Gain", min=min_again, max=max_again, step=0.1,
                            value=max_again, format="%.1f"),
                ],
                label="Gain", group="Main Control",
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_GAIN")
        #
        # self.checkin(
        #     ISwitchVector(
        #         device=self.device, name="CCD_ABORT_EXPOSURE",
        #         elements=[
        #             ISwitch(name="ABORT", label="Abort", value=ISwitchState.OFF),
        #         ],
        #         label="Expose Abort", group="Main Control",
        #         rule=ISwitchRule.ONEOFMANY,
        #     ),
        #     send_defVector=True,
        # )
        # self.CameraVectorNames.append("CCD_ABORT_EXPOSURE")
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
                device=self.device, name="FITS_HEADER",
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
                device=self.device, name="CCD_TEMPERATURE",
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
                device=self.device, name="CCD_COMPRESSION",
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
                device=self.device, name="CCD1",
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
                device=self.device, name="CCD_FRAME_TYPE",
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
        #         device=self.device, name="CCD_FRAME_RESET",
        #         elements=[
        #             ISwitch(name="RESET", label="Reset", value=ISwitchState.OFF),
        #         ],
        #         label="Frame Values", group="Image Settings",
        #         rule=ISwitchRule.ONEOFMANY,
        #     ),
        #     send_defVector=True,
        # )
        # self.CameraVectorNames.append("CCD_FRAME_RESET")
        #
        self.checkin(
            ISwitchVector(
                device=self.device, name="UPLOAD_MODE",
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
                device=self.device, name="UPLOAD_SETTINGS",
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
                device=self.device, name="CCD_FAST_TOGGLE",
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
        #
        # self.checkin(
        #     ISwitchVector(
        #         device=self.device, name="CCD_COOLER",
        #         elements=[
        #             ISwitch(name="COOLER_ON", label="ON", value=ISwitchState.OFF),
        #             ISwitch(name="COOLER_OFF", label="OFF", value=ISwitchState.ON),
        #         ],
        #         label="Cooler", group="Main Control",
        #         rule=ISwitchRule.ONEOFMANY,
        #     ),
        #     send_defVector=True,
        # )
        # self.CameraVectorNames.append("CCD_COOLER")
        # finish
        return True

    def create_BayerFits(self, array, metadata):
        """creates FITS image from raw frame
        """
        # rescale
        bit_depth = self.present_CameraSettings.RawMode["bit_depth"]
        if bit_depth > 8:
            bit_pix = 16
            array = array.view(np.uint16) * (2 ** (bit_pix - bit_depth))
        else:
            bit_pix = 8
            array = array.view(np.uint8) * (2 ** (bit_pix - bit_depth))
        # determine frame type
        FrameType = self.knownVectors["CCD_FRAME_TYPE"].get_OnSwitches()[0]
        # convert to FITS
        hdu = fits.PrimaryHDU(array)
        FitsHeader = [
            ("BZERO", 2 ** (bit_pix - 1), "offset data range"),
            ("BSCALE", 1, "default scaling factor"),
            ("ROWORDER", "TOP-DOWN", "Row order"),
            ("INSTRUME", self.device, "CCD Name"),
            ("TELESCOP", "Unknown", "Telescope name"),  # TODO
            ("OBSERVER", self.knownVectors["FITS_HEADER"]["FITS_OBSERVER"].value, "Observer name"),
            ("OBJECT", self.knownVectors["FITS_HEADER"]["FITS_OBJECT"].value, "Object name"),
            ("EXPTIME", metadata["ExposureTime"]/1e6, "Total Exposure Time (s)"),
            ("CCD-TEMP", metadata.get('SensorTemperature', 0), "CCD Temperature (Celsius)"),
            ("PIXSIZE1", self.CamProps["UnitCellSize"][0] / 1e3, "Pixel Size 1 (microns)"),
            ("PIXSIZE2", self.CamProps["UnitCellSize"][1] / 1e3, "Pixel Size 2 (microns)"),
            ("XBINNING", 1, "Binning factor in width"),
            ("YBINNING", 1, "Binning factor in height"),
            ("XPIXSZ", self.CamProps["UnitCellSize"][0] / 1e3, "X binned pixel size in microns"),
            ("YPIXSZ", self.CamProps["UnitCellSize"][1] / 1e3, "Y binned pixel size in microns"),
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


    def create_RgbFits(self, array, metadata):
        """creates FITS image from RGB frame
        """
        # determine frame type
        FrameType = self.knownVectors["CCD_FRAME_TYPE"].get_OnSwitches()[0]
        # convert to FITS
        hdu = fits.PrimaryHDU(array.transpose([2, 0, 1]))
        FitsHeader = [
            # ("CTYPE3", 'RGB'),  # Is that needed to make it a RGB image?
            ("BZERO", 0, "offset data range"),
            ("BSCALE", 1, "default scaling factor"),
            ("DATAMAX", 255),
            ("DATAMIN", 0),
            #("ROWORDER", "TOP-DOWN", "Row Order"),
            ("INSTRUME", self.device, "CCD Name"),
            ("TELESCOP", "Unknown", "Telescope name"),  # TODO
            ("OBSERVER", self.knownVectors["FITS_HEADER"]["FITS_OBSERVER"].value, "Observer name"),
            ("OBJECT", self.knownVectors["FITS_HEADER"]["FITS_OBJECT"].value, "Object name"),
            ("EXPTIME", metadata["ExposureTime"]/1e6, "Total Exposure Time (s)"),
            ("CCD-TEMP", metadata.get('SensorTemperature', 0), "CCD Temperature (Celsius)"),
            ("PIXSIZE1", self.CamProps["UnitCellSize"][0] / 1e3, "Pixel Size 1 (microns)"),
            ("PIXSIZE2", self.CamProps["UnitCellSize"][1] / 1e3, "Pixel Size 2 (microns)"),
            ("XBINNING", 1, "Binning factor in width"),
            ("YBINNING", 1, "Binning factor in height"),
            ("XPIXSZ", self.CamProps["UnitCellSize"][0] / 1e3, "X binned pixel size in microns"),
            ("YPIXSZ", self.CamProps["UnitCellSize"][1] / 1e3, "Y binned pixel size in microns"),
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


    def do_Exposure(self):
        if self.picam2 is None:
            logging.error("trying to make an exposure without camera opened")
            return False
        # get exposure time, analogue gain, DoFastExposure
        has_RawModes = len(self.RawModes) > 0
        NewCameraSettings = CameraSettings(
            ExposureTime=self.knownVectors["CCD_EXPOSURE"]["CCD_EXPOSURE_VALUE"].value,
            AGain=self.knownVectors["CCD_GAIN"]["GAIN"].value,
            DoFastExposure=self.knownVectors["CCD_FAST_TOGGLE"]["INDI_ENABLED"].value == ISwitchState.ON,
            DoRaw=self.knownVectors["FRAME_TYPE"]["FRAMETYPE_RAW"].value == ISwitchState.ON if has_RawModes else False,
            ProcSize=(int(self.knownVectors["CCD_FRAME"]["WIDTH"].value), int(self.knownVectors["CCD_FRAME"]["HEIGHT"].value)),
            RawMode=self.RawModes[self.knownVectors["RAW_FORMAT"].get_OnSwitchesIdxs()[0]] if has_RawModes else None,
        )
        logging.info(f'new camera settings: {NewCameraSettings}')
        # need a camera stop/start when something has changed on exposure controls
        IsRestartNeeded = self.present_CameraSettings.is_RestartNeeded(NewCameraSettings)
        if self.picam2.started and IsRestartNeeded:
            logging.info(f'stopping camera for reconfiguration: {NewCameraSettings}')
            self.picam2.stop()
        # change of DoFastExposure needs a configuration change
        if self.present_CameraSettings.is_ReconfigurationNeeded(NewCameraSettings):
            # need a new camera configuration
            config = self.picam2.create_still_configuration(
                queue=NewCameraSettings.DoFastExposure,
                buffer_count=2 if NewCameraSettings.DoFastExposure else 1  # need at least 2 buffer for queueing
            )
            if NewCameraSettings.DoRaw:
                # we do not need the main stream and configure it to smaller size to save memory
                config["main"]["size"] = (240, 190)
                # configure raw stream
                config["raw"] = {"size": NewCameraSettings.RawMode["size"], "format": NewCameraSettings.RawMode["camera_format"]}
            else:
                config["main"]["size"] = NewCameraSettings.ProcSize
                config["main"]["format"] = "BGR888"  # strange: we get RBG when configuring HQ camera as BGR
            # optimize (align) configuration: small changes to some main stream configurations
            # (for instance: size) will fit better to hardware
            self.picam2.align_configuration(config)
            # set still configuration
            self.picam2.configure(config)
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
                "ExposureTime": int(NewCameraSettings.ExposureTime * 1e6),  # exposure time in us; needs to be integer!
            }
        )
        # restart if needed
        if IsRestartNeeded:
            self.picam2.start()
            logging.info(f'camera restarted')
        # camera runs now with new parameter
        self.present_CameraSettings = NewCameraSettings
        # get (blocking!) frame and meta data
        (array, ), metadata = self.picam2.capture_arrays(["raw" if self.present_CameraSettings.DoRaw else "main"])
        logging.info("got exposed frame")
        # inform client about progress
        nv = self.knownVectors["CCD_EXPOSURE"]
        nv.state = IVectorState.BUSY
        nv["CCD_EXPOSURE_VALUE"] = 0
        nv.send_setVector()
        # at least HQ camera reports CCD temperature in meta data
        nv = self.knownVectors["CCD_TEMPERATURE"]
        nv["CCD_TEMPERATURE_VALUE"] = metadata.get('SensorTemperature', 0)
        nv.send_setVector()
        # create FITS images
        if self.present_CameraSettings.DoRaw:
            hdul = self.create_BayerFits(array=array, metadata=metadata)
        else:
            hdul = self.create_RgbFits(array=array, metadata=metadata)
        bstream = io.BytesIO()
        hdul.writeto(bstream)
        size = bstream.tell()
        bstream.seek(0)
        # make BLOB
        logging.info(f"sending frame as BLOB: {size}")
        bv = self.knownVectors["CCD1"]
        compress = self.knownVectors["CCD_COMPRESSION"].get_OnSwitches()[0] == "CCD_COMPRESS"
        bv["CCD1"].set_data(data=bstream.read(), format=".fits", compress=compress)
        logging.info(f"sending BLOB")
        bv.send_setVector()
        #
        return True




if __name__ == "__main__":
    device = indi_pylibcamera(config=config, device="indi_pylibcamera")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(device.run())
