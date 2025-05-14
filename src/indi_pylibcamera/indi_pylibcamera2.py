#!/usr/bin/env python3

from .indi_pylibcamera import main as main1


def main(driver_instance="2"):
    main1(driver_instance=driver_instance)

if __name__ == "__main__":
    main()
