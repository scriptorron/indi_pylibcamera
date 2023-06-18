#!/usr/bin/bash
##################################
# build distribution archive
##################################

# create driver XML
cd src
python3 -m indi_pylibcamera.make_driver_xml
mv indi_pylibcamera.xml indi_pylibcamera
cd "$OLDPWD"

# updating build tools may be needed
#python3 -m pip install --upgrade build

# build pip package
python3 -m build
