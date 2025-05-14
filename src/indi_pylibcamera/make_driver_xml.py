#!/usr/bin/env python3
"""
make indi_pylibcamera.xml for integration in EKOS
"""

from __init__ import __version__
from lxml import etree


def make_driver_xml(instance):
    """create driver XML

    Returns:
        ElementTree object with contents of XML file
    """
    driversList = etree.Element("driversList")
    devGroup = etree.SubElement(driversList, "devGroup", {"group": "CCDs"})
    device = etree.SubElement(devGroup, "device", {"label": "INDI pylibcamera" + instance})
    driver = etree.SubElement(device, "driver", {"name": "INDI pylibcamera" + instance})
    driver.text = "indi_pylibcamera" + instance
    version = etree.SubElement(device, "version")
    version.text = str(__version__)
    return etree.ElementTree(driversList)

def write_driver_xml(filename, instance=""):
    make_driver_xml(instance).write(filename, pretty_print=True)


# main entry point
if __name__ == "__main__":
    write_driver_xml(filename="indi_pylibcamera.xml")
    write_driver_xml(filename="indi_pylibcamera2.xml", instance="2")
    write_driver_xml(filename="indi_pylibcamera3.xml", instance="3")
    write_driver_xml(filename="indi_pylibcamera4.xml", instance="4")
    write_driver_xml(filename="indi_pylibcamera5.xml", instance="5")
