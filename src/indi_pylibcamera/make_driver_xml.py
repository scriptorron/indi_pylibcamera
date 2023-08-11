#!/usr/bin/env python3
"""
make indi_pylibcamera.xml for integration in EKOS
"""

from . import __version__


def make_driver_xml():
    """create driver XML

    Returns:
        string with contents of XML file
    """
    return f"""<driversList>
  <devGroup group="CCDs">
    <device label="INDI pylibcamera">
      <driver name="INDI pylibcamera">indi_pylibcamera</driver>
      <version>{__version__}</version>
    </device>
  </devGroup>
</driversList>
"""

def write_driver_xml(filename):
    with open(filename, "w") as fh:
        fh.write(make_driver_xml())


# main entry point
if __name__ == "__main__":
    write_driver_xml(filename="indi_pylibcamera.xml")
