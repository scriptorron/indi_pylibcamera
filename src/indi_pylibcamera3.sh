#!/usr/bin/python3
# -*- coding: utf-8 -*-
# script to start indi_pylibcamera driver without pip installation
import re
import sys
from indi_pylibcamera.indi_pylibcamera3 import main
if __name__ == '__main__':
    sys.argv[0] = re.sub(r'(-script\.pyw|\.exe)?$', '', sys.argv[0])
    sys.exit(main())
