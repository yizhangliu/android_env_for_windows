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

"""Example script demonstrating usage of AndroidEnv."""

from typing import Dict
from absl import app
from absl import flags
from absl import logging

import android_env
from dm_env import specs
import numpy as np

import shutil
import sys
import multiprocessing

FLAGS = flags.FLAGS

# Simulator args.

flags.DEFINE_string('avd_name', 'Nexus_5X_API_29_x86', 'Name of AVD to use.')
flags.DEFINE_string('android_avd_home', 'C:/Users/ThinkPad/.android/avd', 'Path to AVD.')
flags.DEFINE_string('android_sdk_root', 'C:/Users/ThinkPad/AppData/Local/Android/Sdk', 'Path to SDK.')
flags.DEFINE_string('emulator_path', 'C:/Users/ThinkPad/AppData/Local/Android/Sdk/emulator/emulator.exe', 'Path to emulator.')
flags.DEFINE_string('adb_path', 'C:/Users/ThinkPad/AppData/Local/Android/Sdk/platform-tools/adb.exe', 'Path to ADB.')

# Environment args.
flags.DEFINE_string('task_path', 'C:/android_env/apks/vector_pinball/vector_pinball_table_4.textproto', 'Path to task textproto file.')
flags.DEFINE_string('apk_path', 'C:/android_env/apks/vector_pinball/', 'Path to apk file.')


# Experiment args.
flags.DEFINE_integer('num_steps', 10000, 'Number of steps to take.')


def main(_):

  env = android_env.load(
      emulator_path=FLAGS.emulator_path,
      android_sdk_root=FLAGS.android_sdk_root,
      android_avd_home=FLAGS.android_avd_home,
      avd_name=FLAGS.avd_name,
      adb_path=FLAGS.adb_path,
      task_path=FLAGS.task_path,
      apk_path=FLAGS.apk_path,
      run_headless=False,
  )

  action_spec = env.action_spec()

  def get_random_action() -> Dict[str, np.ndarray]:
    """Returns a random AndroidEnv action."""
    action = {}
    for k, v in action_spec.items():
      if isinstance(v, specs.DiscreteArray):
        action[k] = np.random.randint(low=0, high=v.num_values, dtype='I')
      else:
        action[k] = np.random.random(size=v.shape).astype(v.dtype)
    return action

  _ = env.reset()

  for step in range(FLAGS.num_steps):
    action = get_random_action()
    timestep = env.step(action=action)
    reward = timestep.reward
    logging.info('Step: %r/%r, action:%r, reward:%r', step, FLAGS.num_steps, action, reward)
  
  env.close()

if __name__ == '__main__':
  logging.set_verbosity('info')
  logging.set_stderrthreshold('info')
  # flags.mark_flags_as_required(['avd_name', 'task_path'])
  app.run(main)
