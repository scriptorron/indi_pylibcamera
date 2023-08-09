"""
Snooping manager
"""
import logging


class SnoopingManager:
    def __init__(self, parent, to_server_func):
        self.to_server = to_server_func
        self.parent = parent
        # snooped values: dict(device->dict(name->dict(elements)))
        self.snoopedValues = dict()
        # kind->device table
        self.DevicesOfKind = dict()

    def start_Snooping(self, kind: str, device: str, names: list):
        """start snooping of a different driver

        Args:
            kind: type/kind of driver (mount, focusser, ...)
            device: device name to snoop
            names: vector names to snoop
        """
        if kind in self.DevicesOfKind:
            if device != self.DevicesOfKind[kind]:
                # snoop now a different device -> stop snooping of old device
                self.stop_Snooping(kind)
        self.DevicesOfKind[kind] = device
        if device not in self.snoopedValues:
            self.snoopedValues[device] = dict()
        for name in names:
            if name not in self.snoopedValues[device]:
                self.snoopedValues[device][name] = dict()
                # send request to server
                xml = f'<getProperties version="1.7" device="{device}" name="{name}" />'
                self.to_server(xml)

    def stop_Snooping(self, kind: str):
        """stop snooping for given driver kind/type
        """
        if kind in self.DevicesOfKind:
            device = self.DevicesOfKind[kind]
            del self.snoopedValues[device]
            del self.DevicesOfKind[kind]

    def catching(self, device: str, name: str, values: dict):
        """catch values from a snooped device

        Args:
            device: device which is snooped (or not)
            name: vector name
            values: dict(element->value)
        """
        if device in self.snoopedValues:
            if name in self.snoopedValues[device]:
                self.snoopedValues[device][name] = values
                logging.debug(f'snooped "{device}" - "{name}": {values}')
                if ("DO_SNOOPING" in self.parent.knownVectors) and ("SNOOP" in self.parent.knownVectors["DO_SNOOPING"].get_OnSwitches()):
                    if name in self.parent.knownVectors:
                        self.parent.knownVectors[name].set_byClient(values)

    def get_Elements(self, kind: str, name: str):
        """get elements of snooped vector with given kind

        Args:
            kind: kind/type of snooped device (mount, focusser, ...)
            name: vector name

        Returns:
            dict of vector elements and their values (strings!)
            empty dict if not snooped or nothing received yet
        """
        if kind in self.DevicesOfKind:
            device = self.DevicesOfKind[kind]
            return self.snoopedValues[device].get(name, dict())
        else:
            return dict()

    def __str__(self):
        """make string representation of snooped values
        """
        snooped = []
        for device, deviceProps in self.snoopedValues.items():
            for name, elements in deviceProps.items():
                snooped.append(f'"{device}" - "{name}": {elements}')
        return "\n".join(snooped)

