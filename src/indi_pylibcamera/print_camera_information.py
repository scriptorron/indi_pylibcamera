#!/usr/bin/env python3

import sys
from picamera2 import Picamera2
import pprint

def main():
    # important libraries and their versions
    print("Testing numpy:")
    try:
        import numpy
    except BaseException as e:
        print(e)
    else:
        print(f'  numpy {numpy.__version__}')
    print()
    print("Testing astropy:")
    try:
        import astropy
    except BaseException as e:
        print(e)
    else:
        print(f'  astropy {astropy.__version__}')
    print()

    # list of available cameras:
    cameras = Picamera2.global_camera_info()
    print(f'Found {len(cameras)} cameras.')
    print()

    for c, camera in enumerate(cameras):
        print(f'Camera {c}:')
        pprint.pprint(cameras[c])
        print()
        picam2 = Picamera2(c)
        print('Camera properties:')
        pprint.pprint(picam2.camera_properties)
        print()
        print("Raw sensor modes:")
        pprint.pprint(picam2.sensor_modes)
        print()
        print('Camera controls:')
        pprint.pprint(picam2.camera_controls)
        print()
        if "ExposureTime" in picam2.camera_controls:
            print('Exposure time:')
            min_exp, max_exp, default_exp = picam2.camera_controls["ExposureTime"]
            print(f'  min: {min_exp}, max: {max_exp}, default: {default_exp}')
        else:
            print("ERROR: ExposureTime not in camera controls!")
        print()
        if "AnalogueGain" in picam2.camera_controls:
            print('AnalogGain:')
            min_again, max_again, default_again = picam2.camera_controls["AnalogueGain"]
            print(f'  min: {min_again}, max: {max_again}, default: {default_again}')
        else:
            print("ERROR: AnalogueGain not in camera controls!")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
