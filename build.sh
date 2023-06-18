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

# upload to TestPyPi
#python3 -m twine upload --repository testpypi dist/*
# after upload the package will be visible in https://test.pypi.org/project/indi_pylibcamera
# to test the pip installation from TestPyPi:
# - create virtual environment
# - python3 -m pip install --index-url https://test.pypi.org/simple/ --no-deps indi_pylibcamera

# upload to PyPi
# python3 -m twine upload dist/*
# to test pip installation:
# python3 -m pip install
