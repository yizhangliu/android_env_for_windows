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

"""Tests for android_env.components.logcat_thread."""

import re
import subprocess
import threading
from typing import Match, Pattern

from absl.testing import absltest
from android_env.components import logcat_thread
from android_env.proto import task_pb2
import mock


class FakeStream():
  """This class simulates the logs coming from ADB."""

  def __init__(self):
    self._values = []
    self._kill = False
    self._lock = threading.Lock()

  def send_value(self, value):
    with self._lock:
      self._values.append(value)

  def has_next_value(self):
    return bool(self._values)

  def kill(self):
    self._kill = True

  def __iter__(self):
    while True:
      if self._kill:
        return
      if not self._values:
        continue
      else:
        with self._lock:
          next_value = self._values.pop(0)
        yield next_value


def _log_parsing_config():
  """Returns log_parsing_config object for testing."""

  return task_pb2.LogParsingConfig(
      filters=['AndroidRLTask:V'])


class FakeProc():
  """Fake process that exposes a fake stdout stream."""

  def __init__(self):
    self.stdout = FakeStream()
    self.is_alive = True

  def kill(self):
    self.stdout.kill()
    self.is_alive = False


def make_stdout(data):
  """Returns a valid log output with given data as message."""
  return '         1553110400.424  5583  5658 D Tag: %s' % data


class LogcatThreadTest(absltest.TestCase):

  def setUp(self):
    super().setUp()
    self.mock_popen = self.enter_context(
        mock.patch.object(subprocess, 'Popen', autospec=True))
    self.fake_proc = FakeProc()
    self.mock_popen.return_value = self.fake_proc

  def tearDown(self):
    self.fake_proc.stdout.kill()
    super().tearDown()

  def test_cmd(self):
    _ = logcat_thread.LogcatThread(
        adb_command_prefix=['adb_bin', '-P', '12345', '-s', 'my_device'],
        log_parsing_config=_log_parsing_config())
    expected_cmd = [
        'adb_bin', '-P', '12345', '-s', 'my_device', 'logcat', '-v', 'epoch',
        'AndroidRLTask:V', '*:S',
    ]
    self.mock_popen.assert_called_once_with(
        expected_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True)

  def test_cmd_with_filters(self):
    _ = logcat_thread.LogcatThread(
        adb_command_prefix=['adb', '-P', '5037'],
        log_parsing_config=_log_parsing_config())
    expected_cmd = [
        'adb', '-P', '5037', 'logcat', '-v', 'epoch', 'AndroidRLTask:V', '*:S'
    ]
    self.mock_popen.assert_called_once_with(
        expected_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True)

  def test_kill(self):
    logcat = logcat_thread.LogcatThread(
        adb_command_prefix=['adb', '-P', '5037'],
        log_parsing_config=_log_parsing_config())
    self.assertTrue(self.fake_proc.is_alive)
    logcat.kill()
    self.assertFalse(self.fake_proc.is_alive)

  def test_listeners(self):
    """Ensures that we can wait for a specific message without polling."""
    logcat = logcat_thread.LogcatThread(
        adb_command_prefix=['adb', '-P', '5037'],
        log_parsing_config=_log_parsing_config(),
        print_all_lines=True)
    self.mock_popen.assert_called_once()

    # Set up a listener that modifies an arbitrary state.
    some_state = False

    def my_handler(event: Pattern[str], match: Match[str]):
      del event, match
      nonlocal some_state
      some_state = True

    # Create a desired event and hook up the listener.
    my_event = re.compile('Hello world')
    listener = logcat_thread.EventListener(my_event, my_handler)
    logcat.add_event_listener(listener)
    self.fake_proc.stdout.send_value('Hi there!')  # This should not match.
    self.assertFalse(some_state)
    self.fake_proc.stdout.send_value(make_stdout('Hello world'))
    logcat.wait(event=my_event, timeout_sec=1.0)
    self.assertTrue(some_state)

    # Waiting for any events should also trigger the listener.
    some_state = False
    self.fake_proc.stdout.send_value(make_stdout('Hello world'))
    logcat.wait(event=None, timeout_sec=1.0)
    self.assertTrue(some_state)

    # After removing the listener, it should not be called anymore.
    some_state = False
    logcat.remove_event_listener(listener)
    self.fake_proc.stdout.send_value(make_stdout('Hello world'))
    logcat.wait(event=my_event, timeout_sec=1.0)
    self.assertFalse(some_state)


if __name__ == '__main__':
  absltest.main()
