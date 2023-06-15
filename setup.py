
from setuptools import setup
from setuptools.command.develop import develop
from setuptools.command.install import install
import os.path
from src.indi_pylibcamera.make_driver_xml import write_driver_xml


def store_driver_xml(filename="/usr/share/indi/indi_pylibcamera.xml"):
    try:
        write_driver_xml(filename=filename)
    except FileNotFoundError as e:
        print(f'ERROR: Can not write driver XML:')
        print(f'    {str(e)}')


class PostDevelopCommand(develop):
    """Post-installation for development mode."""
    def run(self):
        develop.run(self)
        store_driver_xml()


class PostInstallCommand(install):
    """Post-installation for installation mode."""
    def run(self):
        install.run(self)
        store_driver_xml()


setup(
    cmdclass={
        'develop': PostDevelopCommand,
        'install': PostInstallCommand,
    }, 
)
