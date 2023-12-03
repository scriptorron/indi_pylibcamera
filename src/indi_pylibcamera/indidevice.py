"""
implementation of an INDI device
based on INDI protocol v1.7


not supported:
- Light
- LightVector
"""

from lxml import etree
import sys
import os
import logging
import base64
import zlib
import threading
import fcntl
import datetime

from . import SnoopingManager

logger = logging.getLogger(__name__)

# helping functions

def get_TimeStamp():
    """return present system time formated as INDI timestamp
    """
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


# enumerations

class IVectorState:
    """INDI property states
    """
    IDLE = "Idle"
    OK = "Ok"
    BUSY = "Busy"
    ALERT = "Alert"


class IPermission:
    """INDI property permissions
    """
    RO = "ro"
    WO = "wo"
    RW = "rw"


class ISwitchRule:
    """INDI switch rules
    """
    ONEOFMANY = "OneOfMany"
    ATMOST1 = "AtMostOne"
    NOFMANY = "AnyOfMany"


class ISwitchState:
    """INDI switch states
    """
    OFF = "Off"
    ON = "On"


# sending messages to client is done by writing stdout

class UnblockTTY:
    """configure stdout for unblocking write
    """

    # shameless copy from https://stackoverflow.com/questions/67351928/getting-a-blockingioerror-when-printing-or-writting-to-stdout
    def __enter__(self):
        self.fd = sys.stdout.fileno()
        self.flags_save = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        flags = self.flags_save & ~os.O_NONBLOCK
        fcntl.fcntl(self.fd, fcntl.F_SETFL, flags)

    def __exit__(self, *args):
        fcntl.fcntl(self.fd, fcntl.F_SETFL, self.flags_save)


ToServerLock = threading.Lock()  # need serialized output of the different threads!


def to_server(msg: str):
    """send message to client
    """
    with ToServerLock:
        with UnblockTTY():
            sys.stdout.write(msg)
            sys.stdout.flush()


class IProperty:
    """INDI property

    Base class for Text, Number, Switch and Blob properties.
    """

    def __init__(self, name: str, label: str = None, value=None):
        """constructor

        Args:
            name: property name
            label: label shown in client GUI
            value: property value
        """
        self._propertyType = "NotSet"
        self.name = name
        if label:
            self.label = label
        else:
            self.label = name
        self.value = value

    def __str__(self) -> str:
        return f"<Property {self._propertyType} name={self.name}>"

    def __repr__(self) -> str:
        return self.__str__()

    def get_oneProperty(self) -> str:
        """return XML for "oneNumber", "one"Text", "oneSwitch", "oneBLOB" messages
        """
        return f'<one{self._propertyType} name="{self.name}">{self.value}</one{self._propertyType}>'

    def set_byClient(self, value: str) -> str:
        """called when value gets set by client

        Overload this when actions are required.

        Args:
            value: value to set

        Returns:
            error message if failed or empty string if okay
        """
        errmsg = f'setting property {self.name} not implemented'
        logger.error(errmsg)
        return errmsg


class IVector:
    """INDI vector

    Base class for Text, Number, Switch and Blob vectors.
    """

    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str = None, group: str = "",
            state: str = IVectorState.IDLE, perm: str = IPermission.RW,
            timeout: int = 60, timestamp: bool = False, message: str = None,
            is_savable: bool = True,
    ):
        """constructor

        Args:
            device: device name
            name: vector name
            elements: list of INDI elements which build the vector
            label: label shown in client GUI
            group: group shown in client GUI
            state: vector state
            perm: vector permission
            timeout: timeout
            timestamp: send messages with (True) or without (False) timestamp
            message: message send to client
            is_savable: can be saved
        """
        self._vectorType = "NotSet"
        self.device = device
        self.name = name
        self.elements = elements
        self.driver_default = {element.name: element.value for element in self.elements}
        if label:
            self.label = label
        else:
            self.label = name
        self.group = group
        self.state = state
        self.perm = perm
        self.timeout = timeout
        self.timestamp = timestamp
        self.message = message
        self.is_savable = is_savable

    def __str__(self) -> str:
        return f"<Vector {self._vectorType} name={self.name}, device={self.device}>"

    def __repr__(self) -> str:
        return self.__str__()

    def __len__(self) -> int:
        """returns number of elements
        """
        return len(self.elements)

    def __add__(self, val: IProperty) -> list:
        """add an element

        Args:
            val: element (INDI property) to add
        """
        self.elements.append(val)
        return self.elements

    def __getitem__(self, name: str) -> IProperty:
        """get named element

        Args:
            name: name of element to get
        """
        for element in self.elements:
            if element.name == name:
                return element
        raise KeyError(f"{name} not in {self.__str__()}")

    def __setitem__(self, name, val):
        """set value of named element

        This does NOT inform the client about a value change!

        Args:
            name: name of element to set
            val: value to set
        """
        for element in self.elements:
            if element.name == name:
                element.value = val
                return
        raise KeyError(f"{name} not in {self.__str__()}")

    def __iter__(self):
        """element iterator
        """
        for element in self.elements:
            yield element

    def get_defVector(self) -> str:
        """return XML message for "defTextVector", "defNumberVector", "defSwitchVector" or "defBLOBVector"
        """
        xml = f'<def{self._vectorType} device="{self.device}"'
        if hasattr(self, "rule"):  # only for ISwitchVector
            xml += f' rule="{self.rule}"'
        xml += f' perm="{self.perm}" state="{self.state}" group="{self.group}"'
        xml += f' label="{self.label}" name="{self.name}"'
        if self.timeout:
            xml += f' timeout="{self.timeout}"'
        if self.timestamp:
            xml += f' timestamp="{get_TimeStamp()}"'
        if self.message:
            xml += f' message="{self.message}"'
        xml += '>'
        for element in self.elements:
            xml += element.get_defProperty()
        xml += f'</def{self._vectorType}>'
        return xml

    def send_defVector(self, device: str = None):
        """tell client about existence of this vector

        Args:
            device: device name
        """
        if (device is None) or (device == self.device):
            logger.debug(f'send_defVector: {self.get_defVector()}')
            to_server(self.get_defVector())

    def get_delVector(self, msg: str = None) -> str:
        """tell client to delete property vector

        Args:
            msg: message to send with delProperty
        """
        xml = f"<delProperty device='{self.device}' name='{self.name}'"
        if msg:
            xml += f" message='{msg}'"
        xml += "/>"
        return xml

    def send_delVector(self):
        """tell client to remove this vector
        """
        logger.debug(f'send_delVector: {self.get_delVector()}')
        to_server(self.get_delVector())

    def get_setVector(self) -> str:
        """return XML for "set" message (to tell client about new vector data)
        """
        xml = f'<set{self._vectorType} device="{self.device}" name="{self.name}"'
        xml += f' state="{self.state}"'
        if self.timeout:
            xml += f' timeout="{self.timeout}"'
        if self.timestamp:
            xml += f' timestamp="{get_TimeStamp()}"'
        if self.message:
            xml += f' message="{self.message}"'
        xml += '>'
        for element in self.elements:
            xml += element.get_oneProperty()
        xml += f'</set{self._vectorType}>'
        return xml

    def send_setVector(self):
        """tell client about vector data
        """
        logger.debug(f'send_setVector: {self.get_setVector()[:100]}')
        to_server(self.get_setVector())

    def set_byClient(self, values: dict):
        """called when vector gets set by client

        Overload this when actions are required.

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
        else:
            self.state = IVectorState.OK
        self.send_setVector()
        self.message = ""

    def save(self):
        """return Vector state

        Returns:
             None if Vector is not savable
             dict with Vector state
        """
        state = None
        if self.is_savable:
            state = dict()
            state["name"] = self.name
            state["values"] = {element.name: element.value for element in self.elements}
        return state

    def restore_DriverDefault(self):
        """restore driver defaults for savable vector
        """
        if self.is_savable:
            self.set_byClient(self.driver_default)


class IText(IProperty):
    """INDI Text property
    """

    def __init__(self, name: str, label: str = None, value: str = ""):
        super().__init__(name=name, label=label, value=value)
        self._propertyType = "Text"

    def set_byClient(self, value: str) -> str:
        """called when value gets set by client

        Args:
            value: value to set

        Returns:
            error message if failed or empty string if okay
        """
        self.value = value
        return ""

    def get_defProperty(self) -> str:
        """return XML for defText message
        """
        return f'<defText name="{self.name}" label="{self.label}">{self.value}</defText>'


class ITextVector(IVector):
    """INDI Text vector
    """

    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str = None, group: str = "",
            state: str = IVectorState.IDLE, perm: str = IPermission.RW,
            timeout: int = 60, timestamp: bool = False, message: str = None,
            is_savable: bool = True,
    ):
        super().__init__(
            device=device, name=name, elements=elements, label=label, group=group,
            state=state, perm=perm, timeout=timeout, timestamp=timestamp, message=message, is_savable=is_savable,
        )
        self._vectorType = "TextVector"


class INumber(IProperty):
    """INDI Number property
    """

    def __init__(
            self, name: str, value: float, min: float, max: float, step: float = 0,
            label: str = None, format: str = "%f"
    ):
        super().__init__(name=name, label=label, value=value)
        self._propertyType = "Number"
        self.min = min
        self.max = max
        self.step = step
        self.format = format

    def set_byClient(self, value: str) -> str:
        """called when value gets set by client

        Args:
            value: value to set

        Returns:
            error message if failed or empty string if okay
        """
        self.value = min(max(float(value), self.min), self.max)
        return ""

    def get_defProperty(self) -> str:
        """return XML for defNumber message
        """
        xml = f'<defNumber name="{self.name}" label="{self.label}" format="{self.format}"'
        xml += f' min="{self.min}" max="{self.max}" step="{self.step}">{self.value}</defNumber>'
        return xml


class INumberVector(IVector):
    """INDI Number vector
    """

    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str = None, group: str = "",
            state: str = IVectorState.IDLE, perm: str = IPermission.RW,
            timeout: int = 60, timestamp: bool = False, message: str = None,
            is_savable: bool = True,
    ):
        super().__init__(
            device=device, name=name, elements=elements, label=label, group=group,
            state=state, perm=perm, timeout=timeout, timestamp=timestamp, message=message, is_savable=is_savable,
        )
        self._vectorType = "NumberVector"


class ISwitch(IProperty):
    """INDI Switch property
    """

    def __init__(self, name: str, label: str = None, value: str = ISwitchState.OFF):
        super().__init__(name=name, label=label, value=value)
        self._propertyType = "Switch"

    def set_byClient(self, value: str) -> str:
        """called when value gets set by client

        Args:
            value: value to set

        Returns:
            error message if failed or empty string if okay
        """
        self.value = value
        return ""

    def get_defProperty(self) -> str:
        """return XML for defSwitch message
        """
        return f'<defSwitch name="{self.name}" label="{self.label}">{self.value}</defSwitch>'


class ISwitchVector(IVector):
    """INDI Switch vector
    """

    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str = None, group: str = "",
            state: str = IVectorState.IDLE, perm: str = IPermission.RW,
            rule: str = ISwitchRule.ONEOFMANY,
            timeout: int = 60, timestamp: bool = False, message: str = None,
            is_savable: bool = True,
    ):
        super().__init__(
            device=device, name=name, elements=elements, label=label, group=group,
            state=state, perm=perm, timeout=timeout, timestamp=timestamp, message=message, is_savable=is_savable,
        )
        self._vectorType = "SwitchVector"
        self.rule = rule

    def get_OnSwitches(self) -> list:
        """return list of element names which are On
        """
        OnSwitches = []
        for element in self.elements:
            if element.value == ISwitchState.ON:
                OnSwitches.append(element.name)
        return OnSwitches

    def get_OnSwitchesLabels(self) -> list:
        """return list of element labels which are On
        """
        OnSwitches = []
        for element in self.elements:
            if element.value == ISwitchState.ON:
                OnSwitches.append(element.label)
        return OnSwitches

    def get_OnSwitchesIdxs(self) -> list:
        """return list of element indices which are On
        """
        OnSwitchesIdxs = []
        for Idx, element in enumerate(self.elements):
            if element.value == ISwitchState.ON:
                OnSwitchesIdxs.append(Idx)
        return OnSwitchesIdxs

    def update_SwitchStates(self, values: dict) -> str:
        """update switch states according to values and switch rules

        Args:
            values: dict(SwitchName: value) of switch values

        Returns:
            error message if any
        """
        errmsgs = []
        if self.rule == ISwitchRule.NOFMANY:
            for propName, value in values.items():
                errmsg = self[propName].set_byClient(value)
                if len(errmsg) > 0:
                    errmsgs.append(errmsg)
        elif (self.rule == ISwitchRule.ATMOST1) or (self.rule == ISwitchRule.ONEOFMANY):
            for propName, value in values.items():
                if value == ISwitchState.ON:
                    # all others must be OFF
                    for element in self.elements:
                        element.value = ISwitchState.OFF
                errmsg = self[propName].set_byClient(value)
                if len(errmsg) > 0:
                    errmsgs.append(errmsg)
        else:
            raise NotImplementedError(f'unknown switch rule "{self.rule}"')
        message = "; ".join(errmsgs)
        return message

    def set_byClient(self, values: dict):
        """called when vector gets set by client
        Special implementation for ISwitchVector to follow switch rules.

        Overload this when actions are required.

        Args:
            values: dict(propertyName: value) of values to set
        """
        self.message = self.update_SwitchStates(values=values)
        # send updated property values
        if len(self.message) > 0:
            self.state = IVectorState.ALERT
        else:
            self.state = IVectorState.OK
        self.send_setVector()
        self.message = ""


class IBlob(IProperty):
    """INDI BLOB property
    """

    def __init__(self, name: str, label: str = None):
        super().__init__(name=name, label=label)
        self._propertyType = "BLOB"
        self.size = 0
        self.format = "not set"
        self.data = b''
        self.enabled = "Only"

    def set_data(self, data: bytes, format: str = ".fits", compress: bool = False):
        """set BLOB data

        Args:
            data: data bytes
            format: data format
            compress: do ZIP compression (True/False)
        """
        self.size = len(data)
        if compress:
            self.data = zlib.compress(data)
            self.format = format + ".z"
        else:
            self.data = data
            self.format = format

    def get_defProperty(self) -> str:
        """return XML for defBLOB message
        """
        xml = f'<defBLOB name="{self.name}" label="{self.label}"/>'
        return xml

    def get_oneProperty(self) -> str:
        """return XML for oneBLOB message
        """
        xml = ""
        if self.enabled in ["Also", "Only"]:
            xml += f'<oneBLOB name="{self.name}" size="{self.size}" format="{self.format}">'
            xml += base64.b64encode(self.data).decode()
            xml += '</oneBLOB>'
        return xml


class IBlobVector(IVector):
    """INDI BLOB vector
    """

    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str = None, group: str = "",
            state: str = IVectorState.IDLE, perm: str = IPermission.RO,
            timeout: int = 60, timestamp: bool = False, message: str = None,
            is_savable: bool = True,
    ):
        super().__init__(
            device=device, name=name, elements=elements, label=label, group=group,
            state=state, perm=perm, timeout=timeout, timestamp=timestamp, message=message, is_savable=is_savable,
        )
        self._vectorType = "BLOBVector"

    def send_setVector(self):
        """tell client about vector data, special version for IBlobVector to avoid double calculation of setVector
        """
        # logger.debug(f'send_setVector: {self.get_setVector()[:100]}')  # this takes too long!
        to_server(self.get_setVector())


class IVectorList:
    """list of vectors
    """

    def __init__(self, elements: list = [], name="IVectorList"):
        self.elements = elements
        self.name = name

    def __str__(self):
        return f"<VectorList name={self.name}>"

    def __repr__(self):
        return self.__str__()

    def __len__(self) -> int:
        return len(self.elements)

    def __add__(self, val: IVector) -> list:
        self.elements.append(val)
        return self.elements

    def __getitem__(self, name: str) -> IVector:
        for element in self.elements:
            if element.name == name:
                return element
        raise ValueError(f'vector list {self.name} has no vector {name}!')

    def __iter__(self):
        for element in self.elements:
            yield element

    def __contains__(self, name):
        for element in self.elements:
            if element.name == name:
                return True

    def pop(self, name: str) -> IVector:
        """return and remove named vector
        """
        for i in range(len(self.elements)):
            if self.elements[i].name == name:
                return self.elements.pop(i)
        raise ValueError(f'vector list {self.name} has no vector {name}!')

    def send_defVectors(self, device: str = None):
        """send def messages for al vectors
        """
        for element in self.elements:
            element.send_defVector(device=device)

    def send_delVectors(self):
        """send del message for all vectors
        """
        for element in self.elements:
            element.send_delVector()

    def checkin(self, vector: IVector, send_defVector: bool = False):
        """add vector to list

        Args:
            vector: vector to add
            send_defVector: send def message to client (True/False)
        """
        if send_defVector:
            vector.send_defVector()
        self.elements.append(vector)

    def checkout(self, name: str):
        """remove named vector and send del message to client
        """
        self.pop(name).send_delVector()


class indiMessageHandler(logging.StreamHandler):
    """logging message handler for INDI

    allows sending of log messages to client
    """
    def __init__(self, device, timestamp=False):
        super().__init__()
        self.device = device
        self.timestamp = timestamp

    def emit(self, record):
        msg = self.format(record)
        # use etree here to get correct encoding of special characters in msg
        attribs = {"device": self.device, "message": msg}
        if self.timestamp:
            attribs['timestamp'] = get_TimeStamp()
        et = etree.ElementTree(etree.Element("message", attribs))
        to_server(etree.tostring(et, xml_declaration=True).decode("latin"))
        #print(f'DBG MessageHandler: {etree.tostring(et, xml_declaration=True).decode("latin")}', file=sys.stderr)


def handle_exception(exc_type, exc_value, exc_traceback):
    """logging of uncaught exceptions
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.error("Uncaught exception!", exc_info=(exc_type, exc_value, exc_traceback))


def enable_Logging(device, timestamp=False):
    """enable logging to client
    """
    global logger
    logger.setLevel(logging.INFO)
    # console handler
    ch = logging.StreamHandler()
    #ch.setLevel(logging.INFO)
    formatter = logging.Formatter('%(name)s-%(levelname)s- %(message)s')
    ch.setFormatter(formatter)
    # INDI message handler
    ih = indiMessageHandler(device=device, timestamp=timestamp)
    ih.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
    # add the handlers to logger
    logger.addHandler(ch)
    logger.addHandler(ih)
    # log uncought exceptions and forward them to client
    sys.excepthook = handle_exception


class indidevice:
    """general INDI device
    """

    def __init__(self, device: str):
        """constructor

        Args:
            device: device name as shown in client GUI
        """
        self.device = device
        self.running = True
        self.knownVectors = IVectorList(name="knownVectors")
        # lock for device parameter
        self.knownVectorsLock = threading.Lock()
        # snooping
        self.SnoopingManager = SnoopingManager.SnoopingManager(parent=self, to_server_func=to_server, logger=logger)

    def send_Message(self, message: str, severity: str = "INFO", timestamp: bool = False):
        """send message to client

        Args:
            message: message text
            severity: message type, one of "DEBUG", "INFO", "WARN", "INFO"
            timestamp: send timestamp
        """
        xml = f'<message device="{self.device}" message="[{severity}] {message}"'
        if timestamp:
            xml += f' timestamp="{get_TimeStamp()}"'
        xml += f'/>'
        to_server(xml)

    def on_getProperties(self, device=None):
        """action to be done after receiving getProperties request
        """
        self.knownVectors.send_defVectors(device=device)

    def message_loop(self):
        """message loop: read stdin, parse as xml, update vectors and send response to stdout
        """
        inp = ""
        while self.running:

            new_inp = sys.stdin.readline()
            # detect termination of indiserver
            if len(new_inp) == 0:
                return
            inp += new_inp

            # maybe XML is complete
            try:
                xml = etree.fromstring(inp)
                inp = ""
            except etree.XMLSyntaxError as error:
                #logger.debug(f"XML not complete ({error}): {inp}")  # creates too many log messages!
                continue

            logger.debug(f'Parsed data from client:\n{etree.tostring(xml, pretty_print=True).decode()}')
            logger.debug("End client data")

            device = xml.attrib.get('device', None)
            if xml.tag == "getProperties":
                self.on_getProperties(device)
            elif (device is None) or (device == self.device):
                if xml.tag in ["newNumberVector", "newTextVector", "newSwitchVector"]:
                    vectorName = xml.attrib["name"]
                    values = {ele.attrib["name"]: (ele.text.strip() if type(ele.text) is str else "") for ele in xml}
                    try:
                        vector = self.knownVectors[vectorName]
                    except ValueError as e:
                        logger.error(f'unknown vector name {vectorName}')
                    else:
                        logger.debug(f"calling {vector} set_byClient")
                        with self.knownVectorsLock:
                            vector.set_byClient(values)
                else:
                    logger.error(
                        f'could not interpret client request: {etree.tostring(xml, pretty_print=True).decode()}')
            else:
                # can be a snooped device
                if xml.tag in ["setNumberVector", "setTextVector", "setSwitchVector", "defNumberVector",
                               "defTextVector", "defSwitchVector"]:
                    vectorName = xml.attrib["name"]
                    values = {ele.attrib["name"]: (ele.text.strip() if type(ele.text) is str else "") for ele in xml}
                    with self.knownVectorsLock:
                        self.SnoopingManager.catching(device=device, name=vectorName, values=values)
                elif xml.tag == "delProperty":
                    # snooped device got closed
                    pass
                else:
                    logger.error(
                        f'could not interpret client request: {etree.tostring(xml, pretty_print=True).decode()}')

    def checkin(self, vector: IVector, send_defVector: bool = False):
        """add vector to knownVectors list

        Args:
            vector: vector to add
            send_defVector: send def message to client (True/False)
        """
        self.knownVectors.checkin(vector, send_defVector=send_defVector)

    def checkout(self, name: str):
        """remove named vector from knownVectors list and send del message to client
        """
        self.knownVectors.checkout(name)

    def setVector(self, name: str, element: str, value=None, state: IVectorState = None, send: bool = True):
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

    def run(self):
        """start device
        """
        self.message_loop()

    def start_Snooping(self, kind: str, device: str, names: list):
        """start snooping of a different driver

        Args:
            kind: type/kind of driver (mount, focusser, ...)
            device: device name to snoop
            names: vector names to snoop
        """
        self.SnoopingManager.start_Snooping(kind=kind, device=device, names=names)

    def stop_Snooping(self, kind: str):
        """stop snooping for given driver kind/type
        """
        self.SnoopingManager.stop_Snooping(kind=kind)
