#!/usr/bin/env python3
"""
make indi_pylibcamera.xml for integration in EKOS
"""

from __init__ import __version__
from lxml import etree


def make_driver_xml(instances):
    """create driver XML

    Returns:
        ElementTree object with contents of XML file
    """
    driversList = etree.Element("driversList")
    devGroup = etree.SubElement(driversList, "devGroup", {"group": "CCDs"})
    for instance in instances:
        device = etree.SubElement(
            devGroup, "device",
            {"label": "INDI pylibcamera" + instance, "manufacturer": "Raspberry PI"}
        )
        driver = etree.SubElement(device, "driver", {"name": "INDI pylibcamera" + instance})
        driver.text = "indi_pylibcamera" + instance
        version = etree.SubElement(device, "version")
        version.text = str(__version__)
    return etree.ElementTree(driversList)

def write_driver_xml(filename, instances):
    make_driver_xml(instances).write(filename, pretty_print=True, xml_declaration=True, encoding="UTF-8")


# main entry point
if __name__ == "__main__":
    write_driver_xml(filename="indi_pylibcamera.xml", instances=["", "2", "3", "4", "5"])
