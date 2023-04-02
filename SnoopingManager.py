"""
Snooping manager
"""

from indidevice import to_server


class SnoopingManager:
    def __init__(self):
        self.snoopedValues = dict()

    def start_Snooping(self, device: str, name: str):
        if device not in self.snoopedValues:
            self.snoopedValues[device] = dict()
        self.snoopedValues[device][name] = {
            "elements": dict(),
        }
        # send request to server
        xml = f'<getProperties version="1.7" device="{device}" name="{name}" />'
        to_server(xml)

    def stop_Snooping(self, device: str, name: str):
        if device in self.snoopedValues:
            if name in self.snoopedValues[device]:
                del self.snoopedValues[device][name]

    def __str__(self):
        """make string representation of snooped values
        """
        snooped = []
        for device, deviceProps in self.snoopedValues.items():
            for name, values in deviceProps.items():
                snooped.append(f'"{device}" - "{name}": {values["elements"]}')
        return "\n".join(snooped)

