#!/usr/bin/bash

set -o errexit
set -o nounset


PATH=/usr/local/bin:/usr/bin:/bin
export PATH


# indiserver might be in /usr/local
INDI_SERVER=$(which indiserver)
INDI_SERVER_BIN=$(dirname "$INDI_SERVER")
INDI_SERVER_USR=$(dirname "$INDI_SERVER_BIN")

echo "Installing required packages for indi_pylibcamera"
sudo apt-get update
sudo apt-get install \
    python3-picamera2 \
    python3-lxml \
    python3-astropy


VERSION="$(grep -Po '^__version__ = \"\K(.*[^\"])' ${PWD}/indi_pylibcamera.py)"

echo "Creating ${INDI_SERVER_USR}/share/indi/indi_pylibcamera.xml"
sudo tee "${INDI_SERVER_USR}/share/indi/indi_pylibcamera.xml" >/dev/null <<EOL
<driversList>
  <devGroup group="CCDs">
    <device label="INDI pylibcamera">
      <driver name="INDI pylibcamera">indi_pylibcamera</driver>
      <version>${VERSION}</version>
    </device>
  </devGroup>
</driversList>
EOL


GIT_DIR=$(dirname "$0")
cd "$GIT_DIR"

sudo ln -nsf "${PWD}/indi_pylibcamera.py" "${INDI_SERVER_USR}/bin/indi_pylibcamera"
sudo chmod +x indi_pylibcamera.py

cd "$OLDPWD"


echo "You may now start the indiserver"
echo ""
echo "  indiserver indi_pylibcamera"
