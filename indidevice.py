"""
implementation of an INDI device
based on INDI protocol v1.7


not supported:
- Light
- LightVector
- snooping
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


def get_TimeStamp():
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


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


def obsolete_to_server(msg: str):
    chunksize = 512
    while len(msg) > 0:
        sys.stdout.write(msg[:chunksize])
        sys.stdout.flush()
        msg = msg[chunksize:]


class UnblockTTY:
    # shameless copy from https://stackoverflow.com/questions/67351928/getting-a-blockingioerror-when-printing-or-writting-to-stdout
    def __enter__(self):
        self.fd = sys.stdout.fileno()
        self.flags_save = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        flags = self.flags_save & ~os.O_NONBLOCK
        fcntl.fcntl(self.fd, fcntl.F_SETFL, flags)

    def __exit__(self, *args):
        fcntl.fcntl(self.fd, fcntl.F_SETFL, self.flags_save)


def to_server(msg: str):
    with UnblockTTY():
        sys.stdout.write(msg)
        sys.stdout.flush()


class IProperty:
    def __init__(self, name: str, label: str = None, value = None):
        self._propertyType = "NotSet"
        self.name = name
        if label:
            self.label = label
        else:
            self.label = name
        self.value = value

    def __str__(self):
        return f"<Property {self._propertyType} name={self.name}>"

    def __repr__(self):
        return self.__str__()

    def get_oneProperty(self) -> str:
        return f'<one{self._propertyType} name="{self.name}">{self.value}</one{self._propertyType}>'

    def set_byClient(self, value: str) -> str:
        """called when value gets set by client

        Overload this when actions are required.

        Args:
            value: value to set

        Returns:
            error message if failed or empty string if okay
        """
        if self._propertyType == "Number":
            self.value = float(value)
            return ""
        elif self._propertyType in ["Text", "Switch"]:
            self.value = value
            return ""
        else:
            errmsg = f'setting property {self.name} not implemented'
            logging.error(errmsg)
            return errmsg


class IVector:
    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str =None, group: str ="",
            state: str = IVectorState.IDLE, perm: str = IPermission.RW,
            timeout: int = 60, timestamp: bool = False, message: str = None
    ):
        self._vectorType = "NotSet"
        self.device = device
        self.name = name
        self.elements = elements
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

    def __str__(self):
        return f"<Vector {self._vectorType} name={self.name}, device={self.device}>"

    def __repr__(self):
        return self.__str__()

    def __len__(self) -> int:
        return len(self.elements)

    def __add__(self, val: IProperty) -> list:
        self.elements.append(val)
        return self.elements

    def __getitem__(self, name: str) -> IProperty:
        for element in self.elements:
            if element.name == name:
                return element
        raise KeyError(f"{name} not in {self.__str__()}")

    def __setitem__(self, name, val):
        for element in self.elements:
            if element.name == name:
                element.value = val
                return
        raise KeyError(f"{name} not in {self.__str__()}")

    def __iter__(self):
        for element in self.elements:
            yield element

    def get_defVector(self) -> str:
        xml = f'<def{self._vectorType} device="{self.device}"'
        if hasattr(self, "rule"):  # only for ISwitchVector
            xml += f' rule="{self.rule}"'
        xml += f' perm="{self.perm}" state="{self.state}" group="{self.group}"'
        xml += f' label="{self.label}" name="{self.name}"'
        #if self.timeout:
        #    xml += f' timeout="{self.timeout}"'
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
            logging.info(f'send_defVector: {self.get_defVector()}')
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
        logging.info(f'send_delVector: {self.get_delVector()}')
        to_server(self.get_delVector())

    def get_setVector(self) -> str:
        """tell client about new vector data
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
        logging.info(f'send_setVector: {self.get_setVector()[:100]}')
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


class IText(IProperty):
    def __init__(self, name: str, label: str = None, value: str = ""):
        super().__init__(name=name, label=label, value=value)
        self._propertyType = "Text"

    def get_defProperty(self) -> str:
        return f'<defText name="{self.name}" label="{self.label}">{self.value}</defText>'


class ITextVector(IVector):
    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str = None, group: str = "",
            state: str = IVectorState.IDLE, perm: str =IPermission.RW,
            timeout: int = 60, timestamp: bool = False, message: str = None
    ):
        super().__init__(
            device=device, name=name, elements=elements, label=label, group=group,
            state=state, perm=perm, timeout=timeout, timestamp=timestamp, message=message
        )
        self._vectorType = "TextVector"


class INumber(IProperty):
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

    def get_defProperty(self) -> str:
        xml = f'<defNumber name="{self.name}" label="{self.label}" format="{self.format}"'
        xml += f' min="{self.min}" max="{self.max}" step="{self.step}">{self.value}</defNumber>'
        return xml


class INumberVector(IVector):
    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str = None, group: str = "",
            state: str = IVectorState.IDLE, perm: str = IPermission.RW,
            timeout: int = 60, timestamp: bool = False, message: str = None
    ):
        super().__init__(
            device=device, name=name, elements=elements, label=label, group=group,
            state=state, perm=perm, timeout=timeout, timestamp=timestamp, message=message
        )
        self._vectorType = "NumberVector"


class ISwitch(IProperty):
    def __init__(self, name: str, label: str = None, value: str = ISwitchState.OFF):
        super().__init__(name=name, label=label, value=value)
        self._propertyType = "Switch"

    def get_defProperty(self) -> str:
        return f'<defSwitch name="{self.name}" label="{self.label}">{self.value}</defSwitch>'


class ISwitchVector(IVector):
    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str = None, group: str = "",
            state: str = IVectorState.IDLE, perm: str = IPermission.RW,
            rule: str = ISwitchRule.ONEOFMANY,
            timeout: int = 60, timestamp: bool = False, message: str = None
    ):
        super().__init__(
            device=device, name=name, elements=elements, label=label, group=group,
            state=state, perm=perm, timeout=timeout, timestamp=timestamp, message=message
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

    def get_OnSwitchesIdxs(self) -> list:
        """return list of element names which are On
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
    def __init__(self, name: str, label: str = None):
        super().__init__(name=name, label=label)
        self._propertyType = "BLOB"
        self.size = 0
        self.format = "not set"
        self.data = b''
        self.enabled = "Only"

    def set_data(self, data: bytes, format: str =".fits", compress: bool =False):
        self.size = len(data)
        if compress:
            self.data = zlib.compress(data)
            self.format = format + ".z"
        else:
            self.data = data
            self.format = format

    def get_defProperty(self) -> str:
        xml = f'<defBLOB name="{self.name}" label="{self.label}"/>'
        return xml

    def get_oneProperty(self) -> str:
        xml =""
        if self.enabled in ["Also", "Only"]:
            xml += f'<oneBLOB name="{self.name}" size="{self.size}" format="{self.format}">'
            xml += base64.b64encode(self.data).decode()
            xml += '</oneBLOB>'
        return xml


class IBlobVector(IVector):
    def __init__(
            self,
            device: str, name: str, elements: list = [],
            label: str = None, group: str = "",
            state: str = IVectorState.IDLE, perm: str = IPermission.RO,
            timeout: int = 60, timestamp: bool = False, message: str = None
    ):
        super().__init__(
            device=device, name=name, elements=elements, label=label, group=group,
            state=state, perm=perm, timeout=timeout, timestamp=timestamp, message=message
        )
        self._vectorType = "BLOBVector"


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

    def pop(self, name: str) -> IVector:
        for i in range(len(self.elements)):
            if self.elements[i].name == name:
                return self.elements.pop(i)
        raise ValueError(f'vector list {self.name} has no vector {name}!')

    def send_defVectors(self, device: str = None):
        for element in self.elements:
            element.send_defVector(device=device)

    def send_delVectors(self):
        for element in self.elements:
            element.send_delVector()

    def checkin(self, vector: IVector, send_defVector: bool = False):
        if send_defVector:
            vector.send_defVector()
        self.elements.append(vector)

    def checkout(self, name: str):
        self.pop(name).send_delVector()


def send_Message(device: str, message: str, severity: str = "INFO", timestamp: bool = False):
    """send message to client

    Args:
        device: device name
        message: message text
        severity: message type, one of "DEBUG", "INFO", "WARN", "INFO"
        timestamp: send timestamp
    """
    xml = f'<message device="{device}" message="[{severity}] {message}"'
    if timestamp:
        xml += f' timestamp="{get_TimeStamp()}"'
    xml += f'/>'
    logging.info(f'send_Message: {xml}')
    to_server(xml)


class indidevice:
    def __init__(self, device: str):
        self.device = device
        self.running = True
        self.knownVectors = IVectorList(name="knownVectors")
        self.message_loop_thread = None

    def on_getProperties(self, device=None):
        # FIXME: remove after debug!
        #send_Message(device=self.device, message="Hallo Nachricht", severity="ERROR")
        self.knownVectors.send_defVectors(device=device)

    def message_loop(self):
        """message loop: read stdin, parse as xml, update vectors and send response to stdout
        """
        inp = ""
        while self.running:
            inp += sys.stdin.readline()
            # maybe XML is complete
            try:
                xml = etree.fromstring(inp)
                inp = ""
            except etree.XMLSyntaxError as error:
                logging.debug(f"XML not complete ({error}): {inp}")
                continue

            logging.info("Parsed data from client")
            logging.info(etree.tostring(xml, pretty_print=True).decode())
            logging.info("End client data")

            if xml.tag == "getProperties":
                if "device" in xml.attrib:
                    self.on_getProperties(xml.attrib['device'])
                else:
                    self.on_getProperties()
            elif xml.tag in ["newNumberVector", "newTextVector", "newSwitchVector"]:
                vectorName = xml.attrib["name"]
                values = {ele.attrib["name"]: ele.text.strip() for ele in xml}
                device = xml.attrib.get('device', None)
                vector = self.knownVectors[vectorName]
                logging.debug(f"calling {vector} set_byClinet")
                if (device is None) or (vector.device == device):
                    vector.set_byClient(values)
            else:
                logging.error(f'could not interpret client request: {etree.tostring(xml, pretty_print=True).decode()}')

    def checkin(self, vector: IVector, send_defVector: bool = False):
        self.knownVectors.checkin(vector, send_defVector=send_defVector)

    def checkout(self, name: str):
        self.knownVectors.checkout(name)

    def run(self):
        self.message_loop_thread = threading.Thread(target=self.message_loop)
        self.message_loop_thread.start()
