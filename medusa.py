#!/usr/bin/env python3

# Standard library imports
import argparse
import os
import platform
import random
import readline
import re
import subprocess
import sys
import time
import traceback
from urllib.parse import urlparse
from typing import Optional, Union

# Third-party imports
import cmd2
import click
import frida
import requests
import yaml
from pick import pick

# Local application/library specific imports
from libraries.libadb import *
from libraries.Modules import *
from libraries.natives import *
from libraries.Questions import *
from libraries.soc_server import *
from utils.google_trans_new import google_translator

from constants import *
from medusa_android import *
from medusa_ios import *

class CLI(cmd2.Cmd):
    snippets = []
    packages = []
    system_libraries = []
    app_libraries = []
    app_info = {}
    show_commands = ['mods', 'categories', 'all', 'snippets']
    _device = None
    modified = False
    translator = google_translator()
    script = None
    detached = True
    pid = None
    native_handler = None
    native_functions = []
    currentPackage = None
    libname = None
    modManager = ModuleManager()
    package_range = ''
    server = None
    device_controller = None
    prompt = PROMPT

    def __init__(self,
                 interactive=True,
                 time_to_run=None,
                 package_name=None,
                 device_id=None,
                 save_to_file=None):
        super().__init__(
            allow_cli_args=False
        )
        self._callback = None
        self.interactive = interactive
        self.time_to_run = time_to_run
        self.package_name = package_name
        self.device_id = device_id
        self.save_to_file = save_to_file
        self.bind_to(self.observe_device_change)

    @property
    def device(self):
        return self._device

    @device.setter
    def device(self, new_device):
        self._device = new_device
        if self._callback:
            self._callback(new_device)

    def bind_to(self, callback):
        self._callback = callback

    def do_c(self, line) -> None:
        """Usage: c [shell command]
        Run a shell command on the local host."""
        subprocess.run(line, shell=True)

    def do_cc(self, line) -> None:
        """
        Get an adb shell to the connected device (no args)
        """
        subprocess.run(f'adb -s {self.device.id} shell {line}', shell=True)

    def do_clear(self, _) -> None:
        """
        Clear the screen (no args)
        """
        subprocess.run('clear', shell=True)
    
    def observe_device_change(self, _):
        if self._device is not None:
            self.prompt = f'({self._device.id}) ' + PROMPT

    def preloop(self):
        randomized_fg = lambda: tuple(random.randint(0, 255) for _ in range(3))
        click.secho(LOGO, fg=randomized_fg(), bold=True)
        self.do_loaddevice("")

    def do_exit(self, _) -> None:
        """
        Exit MEDUSA
        """
        if self.server:
            self.server.stop()

        agent_path = os.path.join(BASE_DIRECTORY, AGENT)
        scratchpad_path = os.path.join(BASE_DIRECTORY, SCRATCHPAD)

        if os.path.getsize(agent_path) != 0:
            if Polar('Do you want to reset the agent script?').ask():
                open(os.path.join(BASE_DIRECTORY, AGENT), 'w').close()

        if os.path.getsize(scratchpad_path) != 119:
            if Polar('Do you want to reset the scratchpad?').ask():
                self.edit_scratchpad('')

        print('Bye!!')
        sys.exit()
    
    def do_loaddevice(self, _) -> None:
        """
        Load a device in order to interact
        """
        try:
            if self.interactive:
                logger.info('Available devices:\n')
                devices = frida.enumerate_devices()

                for i in range(len(devices)):
                    print(f'{i}) {devices[i]}')
                self.device = devices[
                    Numeric('\nEnter the index of the device to use:', lbound=0, ubound=len(devices) - 1).ask()]
                sys_params = self.device.query_system_parameters()
                if ("android" in sys_params["os"]["name"].lower()):
                    self.os = "android"
                    self.device_controller = android_device(self.device.id)
                    self.device_controller.print_dev_properties()
                    self.medusa_handler = MedusaAndroid(self.device)
                elif ("ios" in sys_params["os"]["name"].lower()):
                    self.os = "ios"
                    self.medusa_handler = MedusaIos()
                else:
                    raise Exception()

            elif self.is_remote_device(self.device_id):
                self.device = frida.get_remote_device(self.device_id)
            else:
                self.device = frida.get_device(self.device_id)
        except Exception as e:
            self.device = frida.get_remote_device()
            if not self.interactive:
                raise self.NonInteractiveTypeError("Device unreachable !")
        finally:
            self.device_controller = android_device(self.device.id)
            # lets start by loading all packages and let the user to filter them out
            if self.interactive:
                self.medusa_handler.init_packages('-3')

def non_interactive_excepthook(exc_type, exc_value, tb):
    if exc_type == NonInteractiveTypeError:
        print("Error in non interactive mode:", exc_type, exc_value)
        traceback.print_tb(tb)
        sys.exit(1)
    else:
        print("Error in non interactive mode:", exc_type, exc_value)
        traceback.print_tb(tb)


def write_recipe(self, filename) -> None:
    try:
        data = ''
        click.echo(click.style("[+] Loading a recipe....", bg='blue', fg='white'))
        if os.path.exists(filename):
            with open(filename, 'r') as file:
                for line in file:
                    if line.startswith('MODULE'):
                        module = line[7:-1]
                        click.echo(click.style(f'\tLoading {module}', fg='yellow'))
                        self.modManager.stage_verbadim(module)
                    else:
                        data += line
            self.modified = True
            if data != '':
                click.echo(click.style("[+] Writing to scratchpad...", bg='blue', fg='white'))
                self.edit_scratchpad(data)
        elif not self.interactive:
            raise NonInteractiveTypeError("Recipe not found!")
        else:
            click.echo(click.style("[!] Recipe not found !", bg='red', fg='white'))
    except Exception as e:
        if not self.interactive:
            raise NonInteractiveTypeError(e)
        else:
            print(e)

if __name__ == '__main__':
    if 'libedit' in readline.__doc__:
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")

    sys.excepthook = non_interactive_excepthook

    parser = argparse.ArgumentParser(
        prog='Medusa',
        description='An extensible and modularized framework that automates processes and techniques practiced during the dynamic analysis of Android Applications.')
    parser.add_argument('-r', '--recipe', help='Use this option to load a session/recipe')
    parser.add_argument('--not-interactive', action='store_true', help='Run Medusa without user interaction (additional parameters required)')
    parser.add_argument('-t', '--time', type=int, help='Run Medusa for T seconds without user interaction')
    parser.add_argument('-p', '--package-name', help='Package name to run')
    parser.add_argument('-d', '--device', help='Device to connect to')
    parser.add_argument('-s', '--save', help='Filename to save the output log')
    args = parser.parse_args()

    # If any argument other than recipe is set,
    # assume the device is an Android device
    cli = CLI()
    if args.recipe:
        if args.not_interactive:
            if not(args.time and args.device and args.save and args.package_name):
                    print('Insufficient parameters for not interactive mode. Exiting...')
                    exit(1)
            write_recipe(args.recipe)
            cli = CLI(False, args.time, args.package_name, args.device, args.save)
        elif args.time or args.device or args.save or args.package_name:
            print('Non-interactive mode arguments are ignored in interactive mode.')
        else:
            write_recipe(args.recipe)
    cli.cmdloop()