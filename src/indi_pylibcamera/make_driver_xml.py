#!/usr/bin/env python3
"""
make indi_pylibcamera.xml for integration in EKOS
"""

from __init__ import __version__
from lxml import etree


def make_driver_xml():
    """create driver XML

    Returns:
        ElementTree object with contents of XML file
    """
    driversList = etree.Element("driversList")
    devGroup = etree.SubElement(driversList, "devGroup", {"group": "CCDs"})
    device = etree.SubElement(devGroup, "device", {"label": "INDI pylibcamera"})
    driver = etree.SubElement(device, "driver", {"name": "INDI pylibcamera"})
    driver.text = "indi_pylibcamera"
    version = etree.SubElement(device, "version")
    version.text = str(__version__)
    return etree.ElementTree(driversList)

def write_driver_xml(filename):
    make_driver_xml().write(filename, pretty_print=True)


# main entry point
if __name__ == "__main__":
    write_driver_xml(filename="indi_pylibcamera.xml")
