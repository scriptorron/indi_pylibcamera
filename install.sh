#!/usr/bin/sudo bash

if [ -z "$1" ]; then
    echo "Usage: install.sh <path>/indy_pylibcamera.py"
    exit 1
fi

cat > /usr/share/indi/indi_pylibcamera.xml <<EOL
<driversList>
  <devGroup group="CCDs">
    <device label="INDI pylibcamera">
      <driver name="INDI pylibcamera">indi_pylibcamera</driver>
      <version>1.7.0</version>
    </device>
  </devGroup>
</driversList>
EOL

ln -nsf $1 /usr/bin/indi_pylibcamera
chmod +x /usr/bin/indi_pylibcamera
