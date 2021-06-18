# coding=utf-8
# Copyright 2021 DeepMind Technologies Limited.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A class to manage and control an external ADB process."""

import os,sys
import pathlib
import re
import subprocess
import threading
import time
from typing import List, Optional, Sequence, Tuple

from absl import logging
from android_env.components import errors
from android_env.proto import task_pb2
import pexpect

_MAX_INIT_RETRIES = 20
_INIT_RETRY_SLEEP_SEC = 2.0

_DEFAULT_TIMEOUT_SECONDS = 120.0


class AdbController():
  """Manages communication with adb."""

  def __init__(self,
               adb_path: str = 'adb',
               adb_server_port: int = 5037,
               apk_path: str = '',
               device_name: str = '',
               shell_prompt: str = r'generic_x86:/ \$',
               default_timeout: float = _DEFAULT_TIMEOUT_SECONDS):
    self._adb_path = adb_path
    self._adb_server_port = str(adb_server_port)
    self._apk_path = apk_path
    self._device_name = device_name
    self._prompt = shell_prompt
    self._default_timeout = default_timeout

    self._execute_command_lock = threading.Lock()
    self._adb_shell = None
    self._shell_is_ready = False
    # Unset problematic environment variables. ADB commands will fail if these
    # are set. They are normally exported by AndroidStudio.
    if 'ANDROID_HOME' in os.environ:
      del os.environ['ANDROID_HOME']
    if 'ANDROID_ADB_SERVER_PORT' in os.environ:
      del os.environ['ANDROID_ADB_SERVER_PORT']

  def command_prefix(self) -> List[str]:
    """The command for instantiating an adb client to this server."""
    command_prefix = [self._adb_path, '-P', self._adb_server_port]
    if self._device_name:
      command_prefix.extend(['-s', self._device_name])
    return command_prefix

  def init_server(self, timeout: Optional[float] = None):
    """Initialize the ADB server deamon on the given port.

    This function should be called immediately after initializing the first
    adb_controller, and before launching the simulator.

    Args:
      timeout: A timeout to use for this operation. If not set the default
        timeout set on the constructor will be used.
    """
    # Make an initial device-independent call to ADB to start the deamon.
    logging.info(f'Initialize the ADB server__{self._device_name} / {self._adb_server_port}')
    device_name_tmp = self._device_name
    self._device_name = ''
    self._execute_command(['devices'], timeout=timeout)
    time.sleep(0.2)
    # Subsequent calls will use the device name.
    self._device_name = device_name_tmp

  def close(self) -> None:
    """Closes internal threads and processes."""
    logging.info('Closing ADB controller...')
    if self._adb_shell is not None:
      logging.info('Killing ADB shell')
      self._adb_shell.close(force=True)
      self._adb_shell = None
      self._shell_is_ready = False
    logging.info('Done closing ADB controller.')

  def _execute_command(self,
                       args: List[str],
                       timeout: Optional[float] = None,
                       checkStr = '',
                       ) -> Optional[bytes]:
    """Executes an adb command.

    Args:
      args: A list of strings representing each adb argument.
          For example: ['install', '/my/app.apk']
      timeout: A timeout to use for this operation. If not set the default
        timeout set on the constructor will be used.

    Returns:
      The output of running such command as a string, None if it fails.
    """
    # The lock here prevents commands from multiple threads from messing up the
    # output from this AdbController object.
    logging.info(f'_execute_command: {args}')
    with self._execute_command_lock:
      if args and args[0] == 'shell':
        adb_output = self._execute_shell_command(args[1:], timeout=timeout, checkStr=checkStr)
      else:
        adb_output = self._execute_normal_command(args, timeout=timeout, checkStr=checkStr)
    # logging.info('ADB output: %s', adb_output)
    return adb_output

  def _execute_normal_command(
      self,
      args: List[str],
      timeout: Optional[float] = None,
      checkStr = ''
      ) -> Optional[bytes]:
    """Executes `adb args` and returns its output."""

    timeout = self._resolve_timeout(timeout)
    command = self.command_prefix() + args

    logging.info('Executing ADB command: %s', command)

    try:
      cmd_output = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=timeout)
      # print(f'____cmd_output____{cmd_output}___')
      logging.info('Done executing ADB command: %s', command)
      return cmd_output
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
      logging.exception('Failed to execute ADB command %s', command)
      raise error

  def _execute_shell_command(self,
                             args: List[str],
                             timeout: Optional[float] = None,
                             max_num_retries: int = 3,
                             checkStr = '',
                             ) -> Optional[bytes]:
    """Execute shell command."""

    timeout = self._resolve_timeout(timeout)
    if not self._shell_is_ready:
      self._init_shell(timeout=timeout)
    shell_args = ' '.join(args)
    logging.info('Executing ADB shell command(%d): %s', timeout, shell_args)

    num_tries = 0
    while num_tries < max_num_retries:
      num_tries += 1
      try:
        self._adb_shell.sendline(shell_args)

        if sys.platform == 'win32' and checkStr != '':
            ret = self._adb_shell.expect(checkStr, timeout=timeout)
        else:
            ret = self._adb_shell.expect(self._prompt, timeout=timeout)
        # logging.info(f'Executing ADB shell command_2_: {ret} / {self._adb_shell.before}/ {self._adb_shell.after}')
        
        logging.info('Done executing ADB shell command: %s', shell_args)
        if sys.platform == 'win32':
            output = self._adb_shell.after
        else:
            shell_ret = self._adb_shell.before.partition('\n'.encode('utf-8'))
            logging.info(f'__shell_ret: {shell_ret} ')
            output = shell_ret[2]  # Consume command.
        return output
      except pexpect.exceptions.EOF:
        logging.exception('Shell command failed_1. Reinitializing the shell.')
        logging.warning('self._adb_shell.before: %r', self._adb_shell.before)
        self._init_shell(timeout=timeout)
      except pexpect.exceptions.TIMEOUT:        
        logging.warning(f'pexpect.exceptions.TIMEOUT___')
        if sys.platform == 'win32' and self._adb_shell.before != b'':
            output = self._adb_shell.before
            self._adb_shell._set_buffer(b'')
            self._adb_shell.before = b''
            self._adb_shell.after = b''
            if checkStr != '':
                output = str.encode(checkStr)
            return output
        else:
            logging.exception('Shell command failed_2. Reinitializing the shell.')
            self._init_shell(timeout=timeout)

    logging.exception('Reinitializing the shell did not solve the issue.')
    raise errors.AdbControllerPexpectError()

  def _init_shell(self, timeout: Optional[float] = None) -> None:
    """Starts an ADB shell process.

    Args:
      timeout: A timeout to use for this operation. If not set the default
        timeout set on the constructor will be used.

    Raises:
        errors.AdbControllerShellInitError when adb shell cannot be initialized.
    """

    timeout = self._resolve_timeout(timeout)
    command = ' '.join(self.command_prefix() + ['shell'])
    logging.info('Initialising ADB shell with command: %s', command)

    num_tries = 0
    while num_tries < _MAX_INIT_RETRIES:
      num_tries += 1
      try:
        logging.info(f'Spawning ADB shell...{sys.platform}')        
        if sys.platform == 'win32':
            self._adb_shell = pexpect.popen_spawn.PopenSpawn(command, timeout=timeout)
        else:
            self._adb_shell = pexpect.spawn(command, use_poll=True, timeout=timeout)
        
        # Setting this to None prevents a 50ms wait for each sendline.
        self._adb_shell.delaybeforesend = None
        self._adb_shell.delayafterread = None
        logging.info(f'Done spawning ADB shell. Consuming first prompt ({timeout} / {num_tries})...')
        if sys.platform == 'win32':
            self._adb_shell.expect(self._prompt, timeout=timeout, async_=True)
        else:
            self._adb_shell.expect(self._prompt, timeout=timeout)
        logging.info(f'Done consuming first prompt.')
        self._shell_is_ready = True
        return
      except (pexpect.ExceptionPexpect, ValueError) as e:
        logging.exception(e)
        logging.error('self._adb_shell.before: %r', self._adb_shell.before)
        logging.error('Could not start ADB shell. Try %r of %r.', num_tries, _MAX_INIT_RETRIES)
        time.sleep(_INIT_RETRY_SLEEP_SEC)

    raise errors.AdbControllerShellInitError(
        'Failed to start ADB shell. Max number of retries reached.')

  def _resolve_timeout(self, timeout: Optional[float]) -> float:
    """Returns the correct timeout to be used for external calls."""
    return self._default_timeout if timeout is None else timeout

  def _wait_for_device(self,
                       max_tries: int = 20,
                       sleep_time: float = 1.0,
                       timeout: Optional[float] = None) -> None:
    """Waits for the device to be ready.

    Args:
      max_tries: Number of times to check if device is ready.
      sleep_time: Sleep time between checks, in seconds.
      timeout: A timeout to use for this operation. If not set the default
        timeout set on the constructor will be used.

    Returns:
      True if the device is ready, False if the device timed out.
    Raises:
      errors.AdbControllerDeviceTimeoutError when the device is not ready after
        exhausting `max_tries`.
    """
    num_tries = 0
    while num_tries < max_tries:
      ready = self._check_device_is_ready(timeout=timeout)
      if ready:
        logging.info('Device is ready.')
        return
      time.sleep(sleep_time)
      logging.error('Device is not ready.')
    raise errors.AdbControllerDeviceTimeoutError('Device timed out.')

  def _check_device_is_ready(self, timeout: Optional[float] = None) -> bool:
    """Checks if the device is ready."""
    logging.info(f'__________________checking device ...___')
    required_services = ['window', 'package', 'input', 'display']
    for service in required_services:
      checkStr = f'Service {service}: found'
      check_output = self._execute_command(['shell', 'service', 'check', service], 
                        timeout=timeout,
                        checkStr = checkStr
                        )
      if not check_output:
        logging.error('Check for service "%s" failed.', service)
        return False
      check_output = check_output.decode('utf-8').strip()
      if check_output != checkStr:
        logging.error(check_output)
        return False
    return True

  # ===== SPECIFIC COMMANDS =====

  def install_binary(self,
                     src: str,
                     dest_dir: str,
                     timeout: Optional[float] = None):
    """Installs the specified binary on the device."""
    self._execute_command(['shell', 'su', '0', 'mkdir', '-p', dest_dir],
                          timeout=timeout)
    self._execute_command(
        ['shell', 'su', '0', 'chown', '-R', 'shell:', dest_dir],
        timeout=timeout)
    bin_name = pathlib.PurePath(src).name
    dest = pathlib.PurePath(dest_dir) / bin_name
    self.push_file(src, str(dest), timeout=timeout)

  def install_apk(self,
                  local_apk_path: str,
                  timeout: Optional[float] = None) -> None:
    """Installs an app given a `local_apk_path` in the filesystem.

    This function checks that `local_apk_path` exists in the file system, and
    will raise an exception in case it doesn't.

    Args:
      local_apk_path: Path to .apk file in the local filesystem.
      timeout: A timeout to use for this operation. If not set the default
        timeout set on the constructor will be used.
    """
    apk_file = self._apk_path + os.sep + local_apk_path
    assert os.path.exists(apk_file), ('Could not find local_apk_path :%r' % apk_file)
    self._execute_command(['install', '-r', '-t', '-g', apk_file], timeout=timeout)

  def is_package_installed(self,
                           package_name: str,
                           timeout: Optional[float] = None) -> bool:
    """Checks that the given package is installed."""
    timeout = 5
    packages = self._execute_command(['shell', 'pm', 'list', 'packages'], timeout=timeout)
    if not packages:
      return False
    packages = packages.decode('utf-8').split()
    # Remove 'package:' prefix for each package.
    packages = [pkg[8:] for pkg in packages if pkg[:8] == 'package:']
    # logging.info('Installed packages: %r', packages)
    if package_name in packages:
      logging.info('Package %s found.', package_name)
      return True
    return False

  def start_activity(self,
                     full_activity: str,
                     extra_args: Optional[List[str]],
                     timeout: Optional[float] = None):
    if extra_args is None:
      extra_args = []
    timeout = 10
    self._execute_command(
        ['shell', 'am', 'start', '-S', '-n', full_activity] + extra_args,
        timeout=timeout)

  def start_intent(self,
                   action: str,
                   data_uri: str,
                   package_name: str,
                   timeout: Optional[float] = None):
    timeout = 10
    self._execute_command(
        ['shell', 'am', 'start', '-a', action, '-d', data_uri, package_name],
        timeout=timeout)

  def start_accessibility_service(self,
                                  accessibility_service_full_name,
                                  timeout: Optional[float] = None):
    timeout = 10
    self._execute_command([
        'shell', 'settings', 'put', 'secure', 'enabled_accessibility_services',
        accessibility_service_full_name
    ],
                          timeout=timeout)

  def broadcast(self,
                receiver: str,
                action: str,
                extra_args: Optional[List[str]],
                timeout: Optional[float] = None):
    if extra_args is None:
      extra_args = []
    timeout = 10
    self._execute_command(
        ['shell', 'am', 'broadcast', '-n', receiver, '-a', action] + extra_args,
        timeout=timeout)

  def setprop(self,
              prop_name: str,
              value: str,
              timeout: Optional[float] = None):
    timeout = 10
    self._execute_command(['shell', 'setprop', prop_name, value],
                          timeout=timeout)

  def push_file(self, src: str, dest: str, timeout: Optional[float] = None):
    timeout = 30
    self._execute_command(['push', src, dest], timeout=timeout)

  def force_stop(self, package: str, timeout: Optional[float] = None):
    timeout = 10
    self._execute_command(['shell', 'am', 'force-stop', package], timeout=timeout)

  def clear_cache(self, package: str, timeout: Optional[float] = None):
    timeout = 10
    self._execute_command(['shell', 'pm', 'clear', package], timeout=timeout, checkStr='Success')

  def grant_permissions(self,
                        package: str,
                        permissions: Sequence[str],
                        timeout: Optional[float] = None):
    for permission in permissions:
      logging.info('Granting permission: %r', permission)
      timeout = 10
      self._execute_command(['shell', 'pm', 'grant', package, permission], timeout=timeout)

  def get_activity_dumpsys(self,
                           package_name: str,
                           timeout: Optional[float] = None) -> Optional[str]:
    """Returns the activity's dumpsys output in a UTF-8 string."""
    timeout = 10
    dumpsys_activity_output = self._execute_command(['shell', 'dumpsys', 'activity', package_name, package_name], timeout=timeout)
    if dumpsys_activity_output:
      return dumpsys_activity_output.decode('utf-8')

  def get_current_activity(self,
                           timeout: Optional[float] = None) -> Optional[str]:
    """Returns the full activity name that is currently opened to the user.

    The format of the output is `package/package.ActivityName', for example:
    "com.example.vokram/com.example.vokram.MainActivity"

    Args:
      timeout: A timeout to use for this operation. If not set the default
        timeout set on the constructor will be used.

    Returns:
      None if no current activity can be extracted.
    """
    timeout = 10
    if sys.platform == 'win32':
        visible_task = self._execute_command(['shell', 'am', 'stack', 'list'], timeout=timeout)
    else:
        visible_task = self._execute_command(['shell', 'am', 'stack', 'list', '|', 'grep', '-E', 'visible=true'], timeout=timeout)
    if not visible_task:
      am_stack_list = self._execute_command(['shell', 'am', 'stack', 'list'], timeout=timeout)
      logging.error('Empty visible_task. `am stack list`: %r', am_stack_list)
      return None

    visible_task = visible_task.decode('utf-8')
    if sys.platform == 'win32':
        visible_task_list = re.findall(r"visible=true topActivity=ComponentInfo{(.+?)}", visible_task)
        if visible_task_list == []:
            visible_task = ''
        else:
            visible_task = 'ComponentInfo{' + visible_task_list[0] + '}'

    p = re.compile(r'.*\{(.*)\}')
    matches = p.search(visible_task)
    if matches is None:
      logging.error(
          'Could not extract current activity. Will return nothing. '
          '`am stack list`: %r',
          self._execute_command(['shell', 'am', 'stack', 'list'],  timeout=timeout))
      return None

    return matches.group(1)

  def start_screen_pinning(self,
                           full_activity: str,
                           timeout: Optional[float] = None):
    current_task_id = self._fetch_current_task_id(full_activity, timeout)
    if current_task_id == -1:
      logging.info('Could not find task ID for activity [%r]', full_activity)
      return  # Don't execute anything if the task ID can't be found.
    timeout = 10
    self._execute_command(['shell', 'am', 'task', 'lock', str(current_task_id)],
                          timeout=timeout)

  def _fetch_current_task_id(self,
                             full_activity_name: str,
                             timeout: Optional[float] = None) -> int:
    """Returns the task ID of the given `full_activity_name`."""
    timeout = 10
    stack = self._execute_command(['shell', 'am', 'stack', 'list'], timeout=timeout)
    stack_utf8 = stack.decode('utf-8')
    lines = stack_utf8.splitlines()

    regex = re.compile(r'^\ *taskId=(?P<id>[0-9]*): %s.*visible=true.*$' % full_activity_name)
    if sys.platform == 'win32':
        regex = re.compile(r'^\ *taskId=(?P<id>[0-9]*): .* visible=true .*{%s}.*' % full_activity_name)
    matches = [regex.search(line) for line in lines]
    # print(f'___matches={matches}___')
    for match in matches:
      if match is None:
        continue
      current_task_id_str = match.group('id')
      try:
        current_task_id = int(current_task_id_str)
        return current_task_id
      except ValueError:
        logging.info('Failed to parse task ID [%r].', current_task_id_str)
    logging.error('Could not find current activity in stack list: %r', stack_utf8)
    # At this point if we could not find a task ID, there's nothing we can do.
    return -1

  def get_screen_dimensions(self,
                            timeout: Optional[float] = None) -> Tuple[int, int]:
    """Returns a (height, width)-tuple representing a screen size in pixels."""
    logging.info(f'Fetching screen dimensions({timeout})...')
    self._wait_for_device(timeout=timeout)
    adb_output = self._execute_command(['shell', 'wm', 'size'], timeout=timeout, checkStr='Physical\ssize:\s([0-9]+x[0-9]+)')
    assert adb_output, 'Empty response from ADB for screen size.'
    adb_output = adb_output.decode('utf-8')
    adb_output = adb_output.replace('\r\n', '')
    # adb_output should be of the form "Physical size: 320x480".
    dims_match = re.match(r'.*Physical\ssize:\s([0-9]+x[0-9]+).*', adb_output)
    assert dims_match, ('Failed to match the screen dimensions. %s' % adb_output)
    dims = dims_match.group(1)
    logging.info('width x height: %s', dims)
    width, height = tuple(map(int, dims.split('x')))  # Split between W & H
    logging.info('Done fetching screen dimensions: (H x W) = (%r, %r)', height, width)
    return (height, width)

  def get_orientation(self, timeout: Optional[float] = None) -> Optional[str]:
    """Returns the device orientation."""
    logging.info('Getting orientation...')
    timeout = 10
    dumpsys = self._execute_command(['shell', 'dumpsys', 'input'],
                                    timeout=timeout)
    # logging.info('dumpsys: %r', dumpsys)
    if not dumpsys:
      logging.error('Empty dumpsys.')
      return None
    dumpsys = dumpsys.decode('utf-8')
    lines = dumpsys.split('\n')  # Split by lines.
    skip_next = False
    for line in lines:
      # There may be multiple devices in dumpsys. An invalid device can be
      # identified by negative PhysicalWidth.
      physical_width = re.match(r'\s+PhysicalWidth:\s+(-?\d+)px', line)
      if physical_width:
        skip_next = int(physical_width.group(1)) < 0

      surface_orientation = re.match(r'\s+SurfaceOrientation:\s+(\d)', line)
      if surface_orientation is not None:
        if skip_next:
          continue
        orientation = surface_orientation.group(1)
        logging.info('Done getting orientation: %r', orientation)
        return orientation

    logging.error('Could not get the orientation. Returning None.')
    return None

  def rotate_device(self,
                    orientation: task_pb2.AdbCall.Rotate.Orientation,
                    timeout: Optional[float] = None) -> None:
    """Sets the device to the given `orientation`."""
    timeout = 10
    self._execute_command(['shell', 'settings', 'put', 'system', 'user_rotation', str(orientation)],
                          timeout=timeout)

  def set_touch_indicators(self,
                           show_touches: bool = True,
                           pointer_location: bool = True,
                           timeout: Optional[float] = None) -> None:
    """Sends command to turn touch indicators on/off."""
    logging.info('Setting show_touches indicator to %r', show_touches)
    logging.info('Setting pointer_location indicator to %r', pointer_location)
    show_touches = 1 if show_touches else 0
    pointer_location = 1 if pointer_location else 0
    self._wait_for_device(timeout=timeout)
    timeout = 10
    self._execute_command(['shell', 'settings', 'put', 'system', 'show_touches', str(show_touches)],
                          timeout=timeout)
    self._execute_command(['shell', 'settings', 'put', 'system', 'pointer_location', str(pointer_location)],
                          timeout=timeout)

  def set_bar_visibility(self,
                         navigation: bool = False,
                         status: bool = False,
                         timeout: Optional[float] = None) -> Optional[bytes]:
    """Show or hide navigation and status bars."""
    command = ['shell', 'settings', 'put', 'global', 'policy_control']
    if status and navigation:  # Show both bars.
      command += ['null*']
    elif not status and navigation:  # Hide status(top) bar.
      command += ['immersive.status=*']
    elif status and not navigation:  # Hide navigation(bottom) bar.
      command += ['immersive.navigation=*']
    else:  # Hide both bars.
      command += ['immersive.full=*']

    timeout = 10
    return self._execute_command(command, timeout=timeout)

  def disable_animations(self, timeout: Optional[float] = None):
    timeout = 10
    self._execute_command(
        ['shell', 'settings put global window_animation_scale 0.0'],
        timeout=timeout)
    self._execute_command(
        ['shell', 'settings put global transition_animation_scale 0.0'],
        timeout=timeout)
    self._execute_command(
        ['shell', 'settings put global animator_duration_scale 0.0'],
        timeout=timeout)

  def input_tap(self, x: int, y: int, timeout: Optional[float] = None) -> None:
    timeout = 10
    self._execute_command(['shell', 'input', 'tap',
                           str(x), str(y)],
                          timeout=timeout)

  def input_text(self,
                 input_text: str,
                 timeout: Optional[float] = None) -> Optional[bytes]:
    timeout = 10
    return self._execute_command(['shell', 'input', 'text', input_text],
                                 timeout=timeout)

  def input_key(self,
                key_code: str,
                timeout: Optional[float] = None) -> Optional[bytes]:
    """Presses a keyboard key.

    Please see https://developer.android.com/reference/android/view/KeyEvent for
    values of `key_code`.

    We currently only accept:

    KEYCODE_HOME (constant 3)
    KEYCODE_BACK (constant 4)
    KEYCODE_ENTER (constant 66)

    Args:
      key_code: The keyboard key to press.
      timeout: Optional time limit in seconds.

    Returns:
      The output of running such command as a string, None if it fails.
    """
    accepted_key_codes = ['KEYCODE_HOME', 'KEYCODE_BACK', 'KEYCODE_ENTER']
    assert key_code in accepted_key_codes, ('Rejected keycode: %r' % key_code)
    timeout = 10
    return self._execute_command(['shell', 'input', 'keyevent', key_code], timeout=timeout)
