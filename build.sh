#!/usr/bin/bash
##################################
# build distribution archive
##################################

# clean up
rm -rf dist
rm -rf src/indi_pylibcamera.egg-info

# create driver XML
cd src
python3 -m indi_pylibcamera.make_driver_xml
mv indi_pylibcamera.xml indi_pylibcamera
cd "$OLDPWD"

# updating build tools may be needed
#python3 -m pip install --upgrade build

# build pip package
python3 -m build
#python3 setup.py sdist

twine check dist/*

# upload to TestPyPi
#   python3 -m twine upload --repository testpypi dist/*
# after upload the package will be visible in https://test.pypi.org/project/indi_pylibcamera
# to test the pip installation from TestPyPi:
# - USE APT_GET TO INSTALL REQUIREMENTS!
#   sudo apt-get install indi-bin python3-picamera2 python3-lxml python3-astropy
# - install in virtual environment
#   python3 -m venv --system-site-packages /home/cam/test_iplc
#   source test_iplc/bin/activate
#   python3 -m pip install --index-url https://test.pypi.org/simple/ indi_pylibcamera
#   indi_pylibcamera_print_camera_information
#   indi_pylibcamera
# - clean-up after test:
#   deactivate
#   rm -rf test_iplc
#   sudo rm /usr/share/indi/indi_pylibcamera.xml
#   pip cache remove indi_pylibcamera*

# upload to PyPi
#   python3 -m twine upload dist/*
# to test pip installation:
# - install in virtual environment
#   python3 -m venv --system-site-packages /home/cam/test_iplc
#   source test_iplc/bin/activate
#   python3 -m pip install indi_pylibcamera
#   indi_pylibcamera_print_camera_information
#   indi_pylibcamera
# - clean-up after test:
#   deactivate
#   rm -rf test_iplc
#   sudo rm /usr/share/indi/indi_pylibcamera.xml
#   pip cache remove indi_pylibcamera*
