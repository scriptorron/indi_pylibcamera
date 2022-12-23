#!/usr/bin/env python3

import sys
from pathlib import Path
import numpy as np
from astropy.io import fits
import io
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
        logging.error(f"DBG set_byClient: {values}")
        self.message = self.update_SwitchStates(values=values)
        # send updated property values
        if len(self.message) > 0:
            self.state = IVectorState.ALERT
            self.send_setVector()
            self.message = ""
            return
        else:
            self.state = IVectorState.OK
        logging.error(f"DBG set_byClient: vor send_setVector")
        self.state = IVectorState.BUSY
        self.send_setVector()
        if self.get_OnSwitches()[0] == "CONNECT":
            logging.error(f"DBG set_byClient: Connect Action")
            if self.parent.open_Camera():
                self.state = IVectorState.OK
            else:
                self.state = IVectorState.ALERT
        else:
            logging.error(f"DBG set_byClient: Disconnect Action")
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
        self.present_ExposureTime = None
        self.present_AGain = None
        self.present_DoFastExposure = None
        self.CamProps = dict()
        self.raw_mode = dict()
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


    def close_Camera(self):
        if self.picam2 is not None:
            for n in self.CameraVectorNames:
                self.checkout(n)
            self.CameraVectorNames = []
            if self.picam2.started:
                self.picam2.stop()
            self.picam2 = None
        self.present_ExposureTime = None
        self.present_AGain = None
        self.present_DoFastExposure = None
        self.CamProps = dict()
        self.raw_mode = dict()


    def open_Camera(self):
        """ opens camera, reads camera properties and still configurations, updates INDI properties
        """
        #
        sv = self.knownVectors["CAMERA_SELECTION"]
        CameraId = None
        for sp in self.knownVectors["CAMERA_SELECTION"].elements:
            if sp.value == ISwitchState.ON:
                CameraId = sp.label
                break
        logging.info(f'connecting to camera {CameraId}')
        if CameraId is None:
            return False
        self.close_Camera()
        CamIdx = self.Cameras.index(CameraId)
        self.picam2 = Picamera2(CamIdx)
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
        # find the best raw camera mode:
        #   * must be unpacked,
        #   * largest number of pixel,
        #   * highest number of bits per pixel 
        n_pixel = -1
        n_bits = -1
        self.raw_mode = None
        for raw_mode in self.picam2.sensor_modes:
            logging.info(f'raw camera mode: {raw_mode}')
            if "unpacked" not in raw_mode.keys():
                raw_mode["unpacked"] = raw_mode[format]
            if raw_mode["unpacked"].endswith("_CSI2P"):
                # CSI2P is  not supported!
                continue
            _n_pixel = raw_mode["size"][0] * raw_mode["size"][1]
            if (_n_pixel > n_pixel) or ((_n_pixel == n_pixel) and (raw_mode["bit_depth"] > n_bits)):
                self.raw_mode = {
                    "size": raw_mode["size"],
                    "format": raw_mode["unpacked"],
                    "bit_depth": raw_mode["bit_depth"],
                }
                n_bits = raw_mode["bit_depth"]
        # some cameras may not have a useful raw mode
        if self.raw_mode is None:
            send_Message(
                device=self.device,
                message="camera does not has a useful raw mode", severity="WARN",
            )
            self.close_Camera()
            return False
        #
        self.checkin(
            INumberVector(
                device=self.device, name="CCD_FRAME",
                elements=[
                    INumber(name="X", label="Left", min=0, max=0, step=0, value=0, format="%4.0f"),
                    INumber(name="Y", label="Top", min=0, max=0, step=0, value=0, format="%4.0f"),
                    INumber(name="WIDTH", label="Width", min=self.raw_mode["size"][0], max=self.raw_mode["size"][0],
                            step=0, value=self.raw_mode["size"][0], format="%4.0f"),
                    INumber(name="HEIGHT", label="Height", min=self.raw_mode["size"][1], max=self.raw_mode["size"][1],
                            step=0, value=self.raw_mode["size"][1], format="%4.0f"),
                ],
                label="Frame", group="Image Settings",
                state=IVectorState.IDLE, perm=IPermission.RO,
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
                            value=self.raw_mode["size"][0], format="%.f"),
                    INumber(name="CCD_MAX_Y", label="Max. Height", min=1, max=1000000, step=0,
                            value=self.raw_mode["size"][1], format="%.f"),
                    INumber(name="CCD_PIXEL_SIZE", label="Pixel size (um)", min=0, max=1000, step=0,
                            value=max(self.CamProps["UnitCellSize"]) / 1e3, format="%.2f"),
                    INumber(name="CCD_PIXEL_SIZE_X", label="Pixel size X", min=0, max=1000, step=0,
                            value=self.CamProps["UnitCellSize"][0] / 1e3, format="%.2f"),
                    INumber(name="CCD_PIXEL_SIZE_Y", label="Pixel size Y", min=0, max=1000, step=0,
                            value=self.CamProps["UnitCellSize"][1] / 1e3, format="%.2f"),
                    INumber(name="CCD_BITSPERPIXEL", label="Bits per pixel", min=0, max=1000, step=0,
                            value=self.raw_mode["bit_depth"], format="%.f"),
                ],
                label="CCD Information", group="Image Info",
                state=IVectorState.IDLE, perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_INFO")
        #
        self.checkin(
            ITextVector(
                device=self.device, name="CCD_CFA",
                elements=[
                    IText(name="CFA_OFFSET_X", label="Offset X", value="0"),
                    IText(name="CFA_OFFSET_Y", label="Offset Y", value="0"),
                    IText(name="CFA_TYPE", label="Type", value=self.raw_mode["format"][1:].rstrip("0123456789")),
                ],
                label="Color filter array", group="Image Info",
                state=IVectorState.IDLE, perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_CFA")
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
                    ISwitch(name="CCD_COMPRESS", label="Compress", value=ISwitchState.OFF),
                    ISwitch(name="CCD_RAW", label="Raw", value=ISwitchState.ON),
                ],
                label="Image", group="Image Settings",
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


    def do_Exposure(self):
        if self.picam2 is None:
            logging.error("trying to make an exposure without camera opened")
            return False
        # get exposure time, analogue gain, DoFastExposure
        ExposureTime = self.knownVectors["CCD_EXPOSURE"]["CCD_EXPOSURE_VALUE"].value
        AGain = self.knownVectors["CCD_GAIN"]["GAIN"].value
        DoFastExposure = False  # FIXME: implement this!
        # need a camera stop/start when something has changed on exposure controls
        IsRestartNeeded = (
                (DoFastExposure != self.present_DoFastExposure)
                or (ExposureTime != self.present_ExposureTime)
                or (AGain != self.present_AGain)
        )
        if self.picam2.started and IsRestartNeeded:
            logging.info(f'stopping camera for reconfiguration: ExposureTime={ExposureTime} sec, AGain={AGain}, DoFastExposure={DoFastExposure}')
            self.picam2.stop()
        # Fast Exposure needs a configuration change
        if DoFastExposure != self.present_DoFastExposure:
            # need a new camera configuration
            config = self.picam2.create_still_configuration(
                queue=DoFastExposure,
                buffer_count=2 if DoFastExposure else 1  # need at least 2 buffer for queueing
            )
            # we do not need the main stream and configure it to smaller size to save memory
            config["main"]["size"] = (480, 380)
            # configure raw stream
            config["raw"] = {"size": self.raw_mode["size"], "format": self.raw_mode["format"]}
            # optimize (align) configuration: small changes to some main stream configurations
            # (for instance: size) will fit better to hardware
            self.picam2.align_configuration(config)
            # set still configuration
            self.picam2.configure(config)
        # exposure time and analog gain are controls
        if (DoFastExposure != self.present_DoFastExposure) or (ExposureTime != self.present_ExposureTime) or (AGain != self.present_AGain):
            self.picam2.set_controls(
                {
                    # controls for main frame: disable all regulations
                    "AeEnable": False,  # AEC/AGC algorithm
                    "NoiseReductionMode": controls.draft.NoiseReductionModeEnum.Off,
                    # disable noise reduction in main frame because it eats stars
                    "AwbEnable": False,  # disable automatic white balance algorithm
                    # controls for raw and main frames
                    "AnalogueGain": AGain,  # max AnalogGain
                    "ExposureTime": int(ExposureTime * 1e6),  # exposure time in us; needs to be integer!
                }
            )
        # restart if needed
        if IsRestartNeeded:
            self.picam2.start()
            logging.info(f'camera restarted')
        # camera runs now with new parameter
        self.present_ExposureTime = ExposureTime
        self.present_AGain = AGain
        self.present_DoFastExposure = DoFastExposure
        # get (blocking) raw frame and meta data
        (array_raw, ), metadata = self.picam2.capture_arrays(["raw"])
        logging.info("got exposed frame")
        # inform client about progress
        nv = self.knownVectors["CCD_EXPOSURE"]
        nv.state = IVectorState.BUSY
        nv["CCD_EXPOSURE_VALUE"] = 0
        nv.send_setVector()
        # rescale
        if self.raw_mode["bit_depth"] > 8:
            bitpix = 16
            array_raw = array_raw.view(np.uint16) * (2 ** (bitpix - self.raw_mode["bit_depth"]))
        else:
            bitpix = 8
            array_raw = array_raw * (2 ** (bitpix - self.raw_mode["bit_depth"]))

        #
        if False:
            with open("Light_001.fits", "rb") as fh:
                f = fh.read()
            size = len(f)
            # make BLOB
            logging.info(f"sending frame as BLOB: {size}")
            bv = self.knownVectors["CCD1"]
            logging.info(f"setting data")
            bv["CCD1"].set_data(data=f, format=".fits", compress=False)
            logging.info(f"sending BLOB")
            bv.send_setVector()
            return True

        #array_raw = np.ones((100, 200), dtype=np.uint16)

        # determine frame type
        FrameType = self.knownVectors["CCD_FRAME_TYPE"].get_OnSwitches()[0]
        # convert to FITS
        hdu = fits.PrimaryHDU(array_raw)
        FitsHeader = [
            ("SIMPLE", True, "file does conform to FITS standard"),
            ("BITPIX", bitpix, "number of bits per data pixel"),
            ("NAXIS", 2, "number of data axes"),
            ("NAXIS1", array_raw.shape[1], "length of data axis 1"),
            ("NAXIS2", array_raw.shape[0], "length of data axis 2"),
            ("EXTEND", True, "FITS dataset may contain extensions"),
            #("COMMENT", RecodeUnicode(FileInfo.get("comment", ""))),
            ("BZERO", 2 ** (bitpix - 1), "offset data range to that of unsigned short"),
            ("BSCALE", 1, "default scaling factor"),
            ("ROWORDER", "TOP-DOWN", "Row Order"),
            #("FILENAME", FileName, "Original filename"),
            ("INSTRUME", self.device, "CCD Name"),
            ("TELESCOP", "Unknown", "Telescope name"),
            ("OBSERVER", "Unknown", "Observer name"),
            ("OBJECT", "Unknown", "Object name"),
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
            #("FILTER", "Red", "Filter"),
            #("FOCALLEN", 900, "Focal Length (mm)"),
            #("APTDIA", 120, "Telescope diameter (mm)"),
            ("XBAYROFF", 0, "X offset of Bayer array"),
            ("YBAYROFF", 0, "Y offset of Bayer array"),
            ("BAYERPAT", self.raw_mode["format"][1:].rstrip("0123456789"), "Bayer color pattern"),
            #("DATE-OBS", time.strftime("%Y-%m-%dT%H:%M:%S.000", time.gmtime(FileInfo.get("TimeStamp", 0.0))), "UTC start date of observation"),
            # more info from camera
            ("AnaGain", metadata.get("AnalogueGain", 0.0), "analog sensor gain"),
            #("CamType", FileInfo.get("CameraType", "unknown"), "camera sensor type"),
        ]
        for FHdr in FitsHeader:
            if len(FHdr) > 2:
                hdu.header[FHdr[0]] = (FHdr[1], FHdr[2])
            else:
                hdu.header[FHdr[0]] = FHdr[1]
        hdul = fits.HDUList([hdu])
        bstream = io.BytesIO()
        hdul.writeto(bstream)
        size = bstream.tell()
        bstream.seek(0)
        # make BLOB
        logging.info(f"sending frame as BLOB: {size}")
        bv = self.knownVectors["CCD1"]
        logging.info(f"setting data")
        bv["CCD1"].set_data(data=bstream.read(), format=".fits", compress=False)
        logging.info(f"sending BLOB")
        bv.send_setVector()
        #
        return True




if __name__ == "__main__":
    device = indi_pylibcamera(config=config, device="indi_pylibcamera")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(device.run())
