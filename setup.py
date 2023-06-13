
from setuptools import setup
from setuptools.command.develop import develop
from setuptools.command.install import install
from subprocess import check_output
import os.path

def create_indi_pylibcamera_xml():
    try:
        indi_server = check_output("which indiserver".split())
    except CalledProcessError:
        print(f'ERROR: Can not find indiserver. Install indiserver first!')
    else:
        indi_server_bin = os.path.dirname(indi_server)
        
        # FIXME: implement this!
        pass


class PostDevelopCommand(develop):
    """Post-installation for development mode."""
    def run(self):
        develop.run(self)
        create_indi_pylibcamera_xml()


class PostInstallCommand(install):
    """Post-installation for installation mode."""
    def run(self):
        install.run(self)
        create_indi_pylibcamera_xml()


setup(
    cmdclass={
        'develop': PostDevelopCommand,
        'install': PostInstallCommand,
    }, 
)
