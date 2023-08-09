#!/usr/bin/env python3

import sys
import os
import os.path
from pathlib import Path
import subprocess
import signal
import traceback

from picamera2 import Picamera2

from configparser import ConfigParser

from . import __version__
from .indidevice import *
from .CameraControl import CameraControl


logging.basicConfig(filename=None, level=logging.INFO, format='%(name)s-%(levelname)s- %(message)s')


def read_config():
    # iterative list of INI files to load
    configfiles = [Path(__file__) / Path("indi_pylibcamera.ini")]
    if "INDI_PYLIBCAMERA_CONFIG_PATH" in os.environ:
        configfiles += [Path(os.environ["INDI_PYLIBCAMERA_CONFIG_PATH"]) / Path("indi_pylibcamera.ini")]
    configfiles += [Path(os.environ["HOME"]) / Path(".indi_pylibcamera") / Path("indi_pylibcamera.ini")]
    configfiles += [Path(os.getcwd()) / Path("indi_pylibcamera.ini")]
    # create config parser instance
    config = ConfigParser()
    config.read(configfiles)
    logging.debug(f"ConfigParser: {config}")
    return config


# INDI vectors with immediate actions

class LoggingVector(ISwitchVector):
    """INDI Switch vector with logging configuration

    Logging verbosity gets changed when client writes this vector.
    """

    def __init__(self, parent):
        self.parent = parent
        LoggingLevel = self.parent.config.get("driver", "LoggingLevel", fallback="Info")
        if LoggingLevel not in ["Debug", "Info", "Warning", "Error"]:
            logging.error('Parameter "LoggingLevel" in INI file has an unsupported value!')
            LoggingLevel = "Info"
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="LOGGING_LEVEL",
            elements=[
                ISwitch(name="LOGGING_DEBUG", label="Debug", value=ISwitchState.ON if LoggingLevel == "Debug" else ISwitchState.OFF),
                ISwitch(name="LOGGING_INFO", label="Info", value=ISwitchState.ON if LoggingLevel == "Info" else ISwitchState.OFF),
                ISwitch(name="LOGGING_WARN", label="Warning", value=ISwitchState.ON if LoggingLevel == "Warning" else ISwitchState.OFF),
                ISwitch(name="LOGGING_ERROR", label="Error", value=ISwitchState.ON if LoggingLevel == "Error" else ISwitchState.OFF),
            ],
            label="Logging", group="Options",
            rule=ISwitchRule.ONEOFMANY,
        )
        self.configure_logger()


    def configure_logger(self):
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


    def set_byClient(self, values: dict):
        """called when vector gets set by client
        special version for changing logging level

        Args:
            values: dict(propertyName: value) of values to set
        """
        logging.debug(f"logging level action: {values}")
        super().set_byClient(values = values)
        self.configure_logger()


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


class RawFormatVector(ISwitchVector):
    """INDI Switch vector to select raw format

    For some cameras the raw format changes binning.
    """

    def __init__(self, parent, CameraThread, do_CameraAdjustments):
        self.parent=parent
        self.CameraThread = CameraThread
        self.do_CameraAdjustments = do_CameraAdjustments
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="RAW_FORMAT",
            elements=[
                ISwitch(name=f'RAWFORMAT{i}', label=rm["label"], value=ISwitchState.ON if i == 0 else ISwitchState.OFF)
                for i, rm in enumerate(self.CameraThread.RawModes)
            ],
            label="Raw format", group="Image Settings",
            rule=ISwitchRule.ONEOFMANY,
        )

    def get_SelectedRawMode(self):
        return self.CameraThread.RawModes[self.get_OnSwitchesIdxs()[0]]

    def update_Binning(self):
        if self.do_CameraAdjustments:
            if self.parent.knownVectors["FRAME_TYPE"]["FRAMETYPE_RAW"].value == ISwitchState.ON:
                # set binning according to raw format
                selectedRawMode = self.CameraThread.RawModes[self.get_OnSwitchesIdxs()[0]]
                binning = selectedRawMode["binning"]
            else:
                # processed frames are all with 1x1 binning
                binning = (1, 1)
            self.parent.setVector("CCD_BINNING", "HOR_BIN", value=binning[0], state=IVectorState.OK, send=False)
            self.parent.setVector("CCD_BINNING", "VER_BIN", value=binning[1], state=IVectorState.OK, send=True)

    def set_byClient(self, values: dict):
        """called when vector gets set by client
        special version for changing raw mode depending binning

        Args:
            values: dict(propertyName: value) of values to set
        """
        super().set_byClient(values=values)
        self.update_Binning()


class RawProcessedVector(ISwitchVector):
    """INDI Switch vector to select raw or processed format

    Processed formats have allways binning = (1,1).
    """

    def __init__(self, parent, CameraThread):
        self.parent=parent
        if len(CameraThread.RawModes) > 0:
            elements = [
                ISwitch(name="FRAMETYPE_RAW", label="Raw", value=ISwitchState.ON),
                ISwitch(name="FRAMETYPE_PROC", label="Processed", value=ISwitchState.OFF),
            ]
        else:
            elements = [
                ISwitch(name="FRAMETYPE_PROC", label="Processed", value=ISwitchState.ON),
            ]
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="FRAME_TYPE",
            elements=elements,
            label="Frame type", group="Image Settings",
            rule=ISwitchRule.ONEOFMANY,
        )

    def set_byClient(self, values: dict):
        """called when vector gets set by client
        special version for changing frame type depending binning

        Args:
            values: dict(propertyName: value) of values to set
        """
        super().set_byClient(values=values)
        self.parent.knownVectors["RAW_FORMAT"].update_Binning()


class BinningVector(INumberVector):
    """INDI Number vector for binning setting

    Binning is related to raw modes: when changing binning the raw mode must also be changed.
    """
    def __init__(self, parent, CameraThread, do_CameraAdjustments):
        self.parent = parent
        self.CameraThread = CameraThread
        self.do_CameraAdjustments = do_CameraAdjustments
        # make dict: binning-->index in CameraThread.RawModes
        self.RawBinningModes = dict()
        for i, rm in enumerate(self.CameraThread.RawModes):
            if not rm["binning"] in self.RawBinningModes:
                self.RawBinningModes[rm["binning"]] = i
        # determine max binning values
        if self.do_CameraAdjustments:
            max_HOR_BIN = 1
            max_VER_BIN = 1
            for binning in self.RawBinningModes.keys():
                max_HOR_BIN = max(max_HOR_BIN, binning[0])
                max_VER_BIN = max(max_VER_BIN, binning[1])
        else:
            max_HOR_BIN = 10
            max_VER_BIN = 10
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="CCD_BINNING",
            elements=[
                INumber(name="HOR_BIN", label="X", min=1, max=max_HOR_BIN, step=1, value=1, format="%2.0f"),
                INumber(name="VER_BIN", label="Y", min=1, max=max_VER_BIN, step=1, value=1, format="%2.0f"),
            ],
            label="Binning", group="Image Settings",
            state=IVectorState.IDLE, perm=IPermission.RW,
        )

    def set_byClient(self, values: dict):
        """called when vector gets set by client
        special version for binning

        Args:
            values: dict(propertyName: value) of values to set
        """
        if self.do_CameraAdjustments:
            # allowed binning depends on FRAME_TYPE (raw or processed) and raw mode
            bestRawIdx = 1
            if self.parent.knownVectors["FRAME_TYPE"]["FRAMETYPE_RAW"].value == ISwitchState.ON:
                # select best matching frame type
                bestError = 1000000
                for binning, RawIdx in self.RawBinningModes.items():
                    err = abs(float(values["HOR_BIN"]) - binning[0]) + abs(float(values["VER_BIN"]) - binning[1])
                    if err < bestError:
                        bestError = err
                        bestRawIdx = RawIdx
            # set fitting raw mode and matching binning
            self.parent.knownVectors["RAW_FORMAT"].set_byClient({f'RAWFORMAT{bestRawIdx}': ISwitchState.ON})
        else:
            super().set_byClient(values=values)


class SnoopingVector(ITextVector):
    """INDI Text vector with other devices to snoop
    """

    def __init__(self, parent):
        self.parent = parent
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="ACTIVE_DEVICES",
            # empty values mean do not snoop
            elements=[
                IText(name="ACTIVE_TELESCOPE", label="Telescope", value=""),
                #IText(name="ACTIVE_ROTATOR", label="Rotator", value=""),
                #IText(name="ACTIVE_FOCUSER", label="Focuser", value=""),
                #IText(name="ACTIVE_FILTER", label="Filter", value=""),
                #IText(name="ACTIVE_SKYQUALITY", label="Sky Quality", value=""),
            ],
            label="Snoop devices", group="Snooping",
        )

    def set_byClient(self, values: dict):
        """called when vector gets set by client
        special version for activating snooping

        Args:
            values: dict(propertyName: value) of values to set
        """
        super().set_byClient(values=values)
        if self.parent.config.getboolean("driver", "DoSnooping", fallback=True):
            for k, v in values.items():
                if k == "ACTIVE_TELESCOPE":
                    self.parent.stop_Snooping(kind="ACTIVE_TELESCOPE")
                    if v != "":
                        self.parent.start_Snooping(
                            kind="ACTIVE_TELESCOPE",
                            device=v,
                            names=[
                                "GEOGRAPHIC_COORD",  # observer site coordinates
                                "EQUATORIAL_EOD_COORD",
                                "EQUATORIAL_COORD",
                                "TELESCOPE_PIER_SIDE",
                                "TELESCOPE_INFO",
                            ]
                        )


class DoSnoopingVector(ISwitchVector):
    """INDI SwitchVector to enable/disable snooping; gets initialized from config file
    """

    def __init__(self, parent):
        self.parent = parent
        config_DoSnooping = self.parent.config.getboolean("driver", "DoSnooping", fallback=True)
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="DO_SNOOPING",
            elements=[
                ISwitch(name="SNOOP", label="Yes", value=ISwitchState.ON if config_DoSnooping else ISwitchState.OFF),
                ISwitch(name="NO_SNOOP", label="No", value=ISwitchState.OFF if config_DoSnooping else ISwitchState.ON),
            ],
            label="Do snooping", group="Snooping",
            rule=ISwitchRule.ONEOFMANY,
        )


class PrintSnoopedValuesVector(ISwitchVector):
    """Button that prints all snooped values as INFO in log
    """

    def __init__(self, parent):
        self.parent = parent
        super().__init__(
            device=self.parent.device, timestamp=self.parent.timestamp, name="PRINT_SNOOPED_VALUES",
            elements=[
                ISwitch(name="PRINT_SNOOPED", label="Print", value=ISwitchState.OFF),
            ],
            label="Print snooped values", group="Snooping",
            rule=ISwitchRule.ATMOST1,
        )

    def set_byClient(self, values: dict):
        """called when vector gets set by client
        special version to print snooped values

        Args:
            values: dict(propertyName: value) of values to set
        """
        logging.debug(f"logging level action: {values}")
        logging.info(f'Snooped values: {str(self.parent.SnoopingManager)}')
        self.state = IVectorState.OK
        self.send_setVector()


def kill_oldDriver():
    """test if another instance of driver is already running and kill it

    This relies on the output of "ps ax" system command.
    Alternative would be 3rd party library psutil which may need to be installed.
    """
    my_PID = os.getpid()
    logging.info(f'my PID: {my_PID}')
    my_fileName = os.path.basename(__file__)[:-3]
    logging.info(f'my file name: {my_fileName}')
    ps_ax = subprocess.check_output(["ps", "ax"]).decode(sys.stdout.encoding)
    ps_ax = ps_ax.split("\n")
    pids_oldDriver = []
    for processInfo in ps_ax:
        if ("python3" in processInfo) and (my_fileName in processInfo):
            PID = int(processInfo.strip().split(" ", maxsplit=1)[0])
            if PID != my_PID:
                logging.info(f'found old driver with PID {PID} ({processInfo})')
                pids_oldDriver.append(PID)
    for pid_oldDriver in pids_oldDriver:
        try:
            os.kill(pid_oldDriver, signal.SIGKILL)
        except ProcessLookupError:
            # process does not exist anymore
            pass
        except PermissionError:
            # not allowed to kill
            logging.error(f'Do not have permission to kill old driver with PID {pid_oldDriver}.')


# the device driver

class indi_pylibcamera(indidevice):
    """camera driver using libcamera
    """

    def __init__(self, config=None):
        """constructor

        Args:
            config: driver configuration
        """
        kill_oldDriver()
        super().__init__(device=config.get("driver", "DeviceName", fallback="indi_pylibcamera"))
        self.config = config
        self.timestamp = self.config.getboolean("driver", "SendTimeStamps", fallback=False)
        # camera
        self.CameraThread = CameraControl(
            parent=self,
            config=config,
        )
        # handle SIGINT and SIGTERM gracefully
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)
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
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="POLLING_PERIOD",
                elements=[
                    INumber(name="PERIOD_MS", label="Period (ms)", min=10, max=600000,
                            step=1000, value=1000, format="%.f"),
                ],
                label="Polling", group="Options",
                perm=IPermission.RW,
            ),
        )
        # snooping
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="GEOGRAPHIC_COORD",
                elements=[
                    INumber(name="LAT", label="Lat (dd:mm:ss.s)", min=-90, max=90, step=0, value=0, format="%012.8m"),
                    INumber(name="LONG", label="Lon (dd:mm:ss.s)", min=0, max=360, step=0, value=0, format="%012.8m"),
                    INumber(name="ELEV", label="Elevation (m)", min=-200, max=10000, step=0, value=0, format="%g"),
                ],
                label="Scope Location", group="Snooping",
                perm=IPermission.RW,
            ),
        )
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="EQUATORIAL_EOD_COORD",
                elements=[
                    INumber(name="RA", label="RA (hh:mm:ss)", min=0, max=24, step=0, value=0, format="%010.6m"),
                    INumber(name="DEC", label="DEC (dd:mm:ss)", min=-90, max=90, step=0, value=0, format="%010.6m"),
                ],
                label="Eq. Coordinates", group="Snooping",
                perm=IPermission.RW,
            ),
        )
        # TODO: "EQUATORIAL_COORD" (J2000 coordinates from mount) are not used!
        if False:
            self.checkin(
                INumberVector(
                    device=self.device, timestamp=self.timestamp, name="EQUATORIAL_COORD",
                    elements=[
                        INumber(name="RA", label="RA (hh:mm:ss)", min=0, max=24, step=0, value=0, format="%010.6m"),
                        INumber(name="DEC", label="DEC (dd:mm:ss)", min=-90, max=90, step=0, value=0, format="%010.6m"),
                    ],
                    label="Eq. J2000 Coordinates", group="Snooping",
                    perm=IPermission.RW,
                ),
            )
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="TELESCOPE_PIER_SIDE",
                elements=[
                    ISwitch(name="PIER_WEST", value=ISwitchState.ON, label="West (pointing east)"),
                    ISwitch(name="PIER_EAST", value=ISwitchState.OFF, label="East (pointing west)"),
                ],
                label="Pier Side", group="Snooping",
                rule=ISwitchRule.ONEOFMANY,
            )
        )
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="TELESCOPE_INFO",
                elements=[
                    INumber(name="TELESCOPE_APERTURE", label="Aperture (mm)", min=10, max=5000, step=0, value=0, format="%g"),
                    INumber(name="TELESCOPE_FOCAL_LENGTH", label="Focal Length (mm)", min=10, max=10000, step=0, value=0, format="%g"),
                    INumber(name="GUIDER_APERTURE", label="Guider Aperture (mm)", min=10, max=5000, step=0, value=0, format="%g"),
                    INumber(name="GUIDER_FOCAL_LENGTH", label="Guider Focal Length (mm)", min=10, max=10000, step=0, value=0, format="%g"),
                ],
                label="Scope Properties", group="Snooping",
                perm=IPermission.RW,
            ),
        )
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="CAMERA_LENS",
                elements=[
                    ISwitch(name="PRIMARY_LENS", value=ISwitchState.ON, label="Primary"),
                    ISwitch(name="GUIDER_LENS", value=ISwitchState.OFF, label="Guide"),
                ],
                label="Camera lens", group="Snooping",
                rule=ISwitchRule.ONEOFMANY,
            )
        )
        self.checkin(
            DoSnoopingVector(parent=self, ),
        )
        self.checkin(
            SnoopingVector(parent=self,),
            send_defVector=True,
        )
        if self.config.getboolean("driver", "PrintSnoopedValuesButton", fallback=False):
            self.checkin(
                PrintSnoopedValuesVector(parent=self, ),
            )

    def exit_gracefully(self, sig, frame):
        """exit driver on system signals
        """
        logging.info("Exit triggered by SIGINT or SIGTERM")
        self.CameraThread.closeCamera()
        traceback.print_stack(frame)
        sys.exit(0)

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
        # allow to select raw or processed frame
        self.checkin(
            RawProcessedVector(parent=self, CameraThread=self.CameraThread),
            send_defVector=True,
        )
        self.CameraVectorNames.append("FRAME_TYPE")
        # raw frame types
        self.checkin(
            RawFormatVector(
                parent=self,
                CameraThread=self.CameraThread,
                do_CameraAdjustments=self.config.getboolean("driver", "CameraAdjustments", fallback=True),
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
                perm=IPermission.RW,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_PROCFRAME")
        # camera controls
        self.addCameraControls()
        #
        self.checkin(
            ExposureVector(parent=self, min_exp=self.CameraThread.min_ExposureTime, max_exp=self.CameraThread.max_ExposureTime),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_EXPOSURE")
        #
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="CCD_ABORT_EXPOSURE",
                elements=[
                    ISwitch(name="ABORT", label="Abort", value=ISwitchState.OFF),
                ],
                label="Abort", group="Main Control",
                rule=ISwitchRule.ATMOST1,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_ABORT_EXPOSURE")
        # CCD_FRAME defines a cropping area in the frame.
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="CCD_FRAME",
                elements=[
                    # ATTENTION: max must be >0
                    INumber(name="X", label="Left", min=0, max=self.CameraThread.getProp("PixelArraySize")[0], step=0, value=0, format="%4.0f"),
                    INumber(name="Y", label="Top", min=0, max=self.CameraThread.getProp("PixelArraySize")[1], step=0, value=0, format="%4.0f"),
                    INumber(name="WIDTH", label="Width", min=1, max=self.CameraThread.getProp("PixelArraySize")[0],
                            step=0, value=self.CameraThread.getProp("PixelArraySize")[0], format="%4.0f"),
                    INumber(name="HEIGHT", label="Height", min=1, max=self.CameraThread.getProp("PixelArraySize")[1],
                            step=0, value=self.CameraThread.getProp("PixelArraySize")[1], format="%4.0f"),
                ],
                label="Frame", group="Image Info",
                perm=IPermission.RO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_FRAME")
        # TODO: implement functionality
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="CCD_FRAME_RESET",
                elements=[
                    ISwitch(name="RESET", label="Reset", value=ISwitchState.OFF),
                ],
                label="Frame Values", group="Image Settings",
                rule=ISwitchRule.ONEOFMANY, perm=IPermission.WO,
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_FRAME_RESET")
        #
        self.checkin(
            BinningVector(
                parent=self,
                CameraThread=self.CameraThread,
                do_CameraAdjustments=self.config.getboolean("driver", "CameraAdjustments", fallback=True),
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_BINNING")
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
        #
        self.checkin(
            ISwitchVector(
                device=self.device, timestamp=self.timestamp, name="CCD_COMPRESSION",
                elements=[
                    # The CCD Simulator has here other names which are not conform to protocol specification:
                    # INDI_ENABLED and INDI_DISABLED
                    #ISwitch(name="INDI_ENABLED", label="Compressed", value=ISwitchState.OFF),
                    #ISwitch(name="INDI_DISABLED", label="Uncompressed", value=ISwitchState.ON),
                    # Specification conform names are: CCD_COMPRESS and CCD_RAW
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
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_FAST_COUNT")
        #
        self.checkin(
            INumberVector(
                device=self.device, timestamp=self.timestamp, name="CCD_GAIN",
                elements=[
                    INumber(name="GAIN", label="Analog Gain", min=self.CameraThread.min_AnalogueGain,
                            max=self.CameraThread.max_AnalogueGain, step=0.1,
                            value=self.CameraThread.max_AnalogueGain, format="%.1f"),
                ],
                label="Gain", group="Main Control",
            ),
            send_defVector=True,
        )
        self.CameraVectorNames.append("CCD_GAIN")
        #
        # Maybe needed: CCD_CFA
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
        # Maybe needed: CCD_COOLER
        #
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
        # delayed updates
        self.knownVectors["RAW_FORMAT"].update_Binning()  # set binning according to frame type and raw format
        # finish
        return True

    def addCameraControls(self, group="Camera controls", send_defVector=True):
        """add vectors for camera controls

        See picamera2 manual for details. Default values are set for manual exposure control.
        """
        # automatic exposure control
        if "AeEnable" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AEENABLE", label="AeEnable", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="INDI_ENABLED", label="Enabled", value=ISwitchState.OFF),
                        ISwitch(name="INDI_DISABLED", label="Disabled", value=ISwitchState.ON),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AEENABLE")
        #
        if "AeConstraintMode" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AECONSTRAINTMODE", label="AeConstraintMode", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="NORMAL", label="Normal", value=ISwitchState.ON),
                        ISwitch(name="HIGHLIGHT", label="Highlight", value=ISwitchState.OFF),
                        ISwitch(name="SHADOWS", label="Shadows", value=ISwitchState.OFF),
                        ISwitch(name="CUSTOM", label="Custom", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AECONSTRAINTMODE")
        #
        if "AeExposureMode" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AEEXPOSUREMODE", label="AeExposureMode", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="NORMAL", label="Normal", value=ISwitchState.ON),
                        ISwitch(name="SHORT", label="Short", value=ISwitchState.OFF),
                        ISwitch(name="LONG", label="Long", value=ISwitchState.OFF),
                        ISwitch(name="CUSTOM", label="Custom", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AEEXPOSUREMODE")
        #
        if "AeMeteringMode" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AEMETERINGMODE", label="AeMeteringMode", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="CENTREWEIGHTED", label="CentreWeighted", value=ISwitchState.ON),
                        ISwitch(name="SPOT", label="Spot", value=ISwitchState.OFF),
                        ISwitch(name="MATRIX", label="Matrix", value=ISwitchState.OFF),
                        ISwitch(name="CUSTOM", label="Custom", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AEMETERINGMODE")
        # automatic focus control
        if "AfMode" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AFMODE", label="AfMode", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="MANUAL", label="Manual", value=ISwitchState.ON),
                        ISwitch(name="AUTO", label="Auto", value=ISwitchState.OFF),
                        ISwitch(name="CONTINUOUS", label="Continuous", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AFMODE")
        #
        if "AfMetering" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AFMETERING", label="AfMetering", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="AUTO", label="Auto", value=ISwitchState.ON),
                        ISwitch(name="WINDOWS", label="Windows", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AFMETERING")
        #
        if "AfPause" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AFPAUSE", label="AfPause", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="DEFERRED", label="Deferred", value=ISwitchState.ON),
                        ISwitch(name="IMMEDIATE", label="Immediate", value=ISwitchState.OFF),
                        ISwitch(name="RESUME", label="Resume", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AFPAUSE")
        #
        if "AfRange" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AFRANGE", label="AfRange", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="NORMAL", label="Normal", value=ISwitchState.ON),
                        ISwitch(name="MACRO", label="Macro", value=ISwitchState.OFF),
                        ISwitch(name="FULL", label="Full", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AFRANGE")
        #
        if "AfSpeed" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AFSPEED", label="AfSpeed", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="NORMAL", label="Normal", value=ISwitchState.ON),
                        ISwitch(name="FAST", label="Fast", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AFSPEED")
        #
        if "AfTrigger" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AFTRIGGER", label="AfTrigger", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="START", label="Start", value=ISwitchState.ON),
                        ISwitch(name="CANCEL", label="Cancel", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AFTRIGGER")
        # automatic white balance
        if "AwbEnable" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AWBENABLE", label="AwbEnable", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="INDI_ENABLED", label="Enabled", value=ISwitchState.OFF),
                        ISwitch(name="INDI_DISABLED", label="Disabled", value=ISwitchState.ON),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AWBENABLE")
        #
        if "AwbMode" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_AWBMODE", label="AwbMode", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="AUTO", label="Auto", value=ISwitchState.ON),
                        ISwitch(name="TUNGSTEN", label="Tungsten", value=ISwitchState.OFF),
                        ISwitch(name="FLUORESCENT", label="Fluorescent", value=ISwitchState.OFF),
                        ISwitch(name="INDOOR", label="Indoor", value=ISwitchState.OFF),
                        ISwitch(name="DAYLIGHT", label="Daylight", value=ISwitchState.OFF),
                        ISwitch(name="CLOUDY", label="Cloudy", value=ISwitchState.OFF),
                        ISwitch(name="CUSTOM", label="Custom", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_AWBMODE")
        # brightness, contrast and color adjustments
        if "Brightness" in self.CameraThread.camera_controls:
            self.checkin(
                INumberVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_BRIGHTNESS", label="Brightness",
                    elements=[
                        INumber(name="BRIGHTNESS", label="Brightness", min=-1.0, max=1.0, step=0.1, value=0.0, format="%.1f"),
                    ],
                ),
            )
            self.CameraVectorNames.append("CAMCTRL_BRIGHTNESS")
        #
        if "ColourGains" in self.CameraThread.camera_controls:
            self.checkin(
                INumberVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_COLOURGAINS", label="ColourGains",  # only used when CAMCTRL_AWBENABLE disabled
                    elements=[
                        INumber(name="REDGAIN", label="Red gain", min=0.0, max=32.0, step=0.1, value=2.0, format="%.2f"),
                        INumber(name="BLUEGAIN", label="Blue gain", min=0.0, max=32.0, step=0.1, value=2.0, format="%.2f"),
                    ],
                ),
            )
            self.CameraVectorNames.append("CAMCTRL_COLOURGAINS")
        #
        if "Contrast" in self.CameraThread.camera_controls:
            self.checkin(
                INumberVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_CONTRAST", label="Contrast",
                    elements=[
                        INumber(name="CONTRAST", label="Contrast", min=0.0, max=32.0, step=0.1, value=1.0, format="%.2f"),
                    ],
                ),
            )
            self.CameraVectorNames.append("CAMCTRL_CONTRAST")
        #
        if "ExposureValue" in self.CameraThread.camera_controls:
            self.checkin(
                INumberVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_EXPOSUREVALUE", label="ExposureValue",
                    elements=[
                        INumber(name="EXPOSUREVALUE", label="ExposureValue", min=-8.0, max=8.0, step=0.1, value=0.0, format="%.1f"),
                    ],
                ),
            )
            self.CameraVectorNames.append("CAMCTRL_EXPOSUREVALUE")
        # misc
        if "NoiseReductionMode" in self.CameraThread.camera_controls:
            self.checkin(
                ISwitchVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_NOISEREDUCTIONMODE", label="NoiseReductionMode", rule=ISwitchRule.ONEOFMANY,
                    elements=[
                        ISwitch(name="OFF", label="Off", value=ISwitchState.ON),
                        ISwitch(name="FAST", label="Fast", value=ISwitchState.OFF),
                        ISwitch(name="HIGHQUALITY", label="HighQuality", value=ISwitchState.OFF),
                    ],
                ),
                send_defVector=send_defVector,
            )
            self.CameraVectorNames.append("CAMCTRL_NOISEREDUCTIONMODE")
        #
        if "Saturation" in self.CameraThread.camera_controls:
            self.checkin(
                INumberVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_SATURATION", label="Saturation",
                    elements=[
                        INumber(name="SATURATION", label="Saturation", min=0.0, max=32.0, step=0.1, value=1.0, format="%.2f"),
                    ],
                ),
            )
            self.CameraVectorNames.append("CAMCTRL_SATURATION")
        #
        if "Sharpness" in self.CameraThread.camera_controls:
            self.checkin(
                INumberVector(
                    device=self.device, timestamp=self.timestamp, group=group,
                    name="CAMCTRL_SHARPNESS", label="Sharpness",
                    elements=[
                        INumber(name="SHARPNESS", label="Sharpness", min=0.0, max=16.0, step=0.1, value=0.0, format="%.2f"),
                    ],
                ),
            )
            self.CameraVectorNames.append("CAMCTRL_SHARPNESS")


    def startExposure(self, exposuretime):
        """start single or fast exposure

        Args:
            exposuretime: exposure time (seconds)
        """
        self.CameraThread.startExposure(exposuretime)


# main entry point
def main():
    device = indi_pylibcamera(config=read_config())
    device.run()
    return 0


if __name__ == "__main__":
    main()
