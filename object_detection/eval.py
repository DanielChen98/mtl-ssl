# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
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
# ==============================================================================

r"""Evaluation executable for detection models.

This executable is used to evaluate DetectionModels. There are two ways of
configuring the eval job.

1) A single pipeline_pb2.TrainEvalPipelineConfig file maybe specified instead.
In this mode, the --eval_training_data flag may be given to force the pipeline
to evaluate on training data instead.

Example usage:
    ./eval \
        --logtostderr \
        --checkpoint_dir=path/to/checkpoint_dir \
        --eval_dir=path/to/eval_dir \
        --pipeline_config_path=pipeline_config.pbtxt

2) Three configuration files may be provided: a model_pb2.DetectionModel
configuration file to define what type of DetectionModel is being evaulated, an
input_reader_pb2.InputReader file to specify what data the model is evaluating
and an eval_pb2.EvalConfig file to configure evaluation parameters.

Example usage:
    ./eval \
        --logtostderr \
        --checkpoint_dir=path/to/checkpoint_dir \
        --eval_dir=path/to/eval_dir \
        --eval_config_path=eval_config.pbtxt \
        --model_config_path=model_config.pbtxt \
        --input_config_path=eval_input_config.pbtxt
"""
import functools
import time
import os
import tensorflow as tf

from google.protobuf import text_format
from object_detection import evaluator
from object_detection.builders import input_reader_builder
from object_detection.builders import model_builder
from object_detection.protos import eval_pb2
from object_detection.protos import input_reader_pb2
from object_detection.protos import model_pb2
from object_detection.protos import pipeline_pb2
from object_detection.utils import label_map_util
from global_utils import custom_utils as utils


tf.logging.set_verbosity(tf.logging.INFO)

flags = tf.app.flags
flags.DEFINE_boolean('eval_training_data', False,
                     'If training data should be evaluated for this job.')
flags.DEFINE_string('checkpoint_dir', '',
                    'Directory containing checkpoints to evaluate, typically '
                    'set to `train_dir` used in the training job.')
flags.DEFINE_string('eval_dir', '',
                    'Directory to write eval summaries to.')
flags.DEFINE_string('pipeline_config_path', '',
                    'Path to a pipeline_pb2.TrainEvalPipelineConfig config '
                    'file. If provided, other configs are ignored')
flags.DEFINE_string('eval_config_path', '',
                    'Path to an eval_pb2.EvalConfig config file.')
flags.DEFINE_string('input_config_path', '',
                    'Path to an input_reader_pb2.InputReader config file.')
flags.DEFINE_string('model_config_path', '',
                    'Path to a model_pb2.DetectionModel config file.')
flags.DEFINE_string("eval_tag", "",
                    """A simple string for discerning a evaluation instance.""")
tf.app.flags.DEFINE_float('gpu_fraction', '0.0',
                          """GPU memory fraction""")
flags.DEFINE_string('eval_label', '', """Testing Label""")
FLAGS = flags.FLAGS


def get_configs_from_pipeline_file():
  """Reads evaluation configuration from a pipeline_pb2.TrainEvalPipelineConfig.

  Reads evaluation config from file specified by pipeline_config_path flag.

  Returns:
    model_config: a model_pb2.DetectionModel
    eval_config: a eval_pb2.EvalConfig
    input_config: a input_reader_pb2.InputReader
  """
  pipeline_config = pipeline_pb2.TrainEvalPipelineConfig()
  with tf.gfile.GFile(FLAGS.pipeline_config_path, 'r') as f:
    text_format.Merge(f.read(), pipeline_config)

  model_config = pipeline_config.model
  eval_config = pipeline_config.eval_config
  if FLAGS.eval_training_data:
    input_config = pipeline_config.train_input_reader
  else:
    input_config = pipeline_config.eval_input_reader

  return model_config, eval_config, input_config


def get_configs_from_multiple_files():
  """Reads evaluation configuration from multiple config files.

  Reads the evaluation config from the following files:
    model_config: Read from --model_config_path
    eval_config: Read from --eval_config_path
    input_config: Read from --input_config_path

  Returns:
    model_config: a model_pb2.DetectionModel
    eval_config: a eval_pb2.EvalConfig
    input_config: a input_reader_pb2.InputReader
  """
  eval_config = eval_pb2.EvalConfig()
  with tf.gfile.GFile(FLAGS.eval_config_path, 'r') as f:
    text_format.Merge(f.read(), eval_config)

  model_config = model_pb2.DetectionModel()
  with tf.gfile.GFile(FLAGS.model_config_path, 'r') as f:
    text_format.Merge(f.read(), model_config)

  input_config = input_reader_pb2.InputReader()
  with tf.gfile.GFile(FLAGS.input_config_path, 'r') as f:
    text_format.Merge(f.read(), input_config)

  return model_config, eval_config, input_config


def get_configs_from_checkpoint_dir():
  """Reads evaluation configuration from checkpoint directory.

  Reads the evaluation config from the following files:
    model_config: Read from FLAGS.checkpoint_dir/model.config
    eval_config: Read from FLAGS.checkpoint_dir/eval.config
    input_config: Read from FLAGS.checkpoint_dir/input.config

  Returns:
    model_config: a model_pb2.DetectionModel
    eval_config: a eval_pb2.EvalConfig
    input_config: a input_reader_pb2.InputReader
  """
  eval_config = eval_pb2.EvalConfig()
  eval_config_path = os.path.join(FLAGS.checkpoint_dir, 'eval.config')
  with tf.gfile.GFile(eval_config_path, 'r') as f:
    text_format.Merge(f.read(), eval_config)

  model_config = model_pb2.DetectionModel()
  model_config_path = os.path.join(FLAGS.checkpoint_dir, 'model.config')
  with tf.gfile.GFile(model_config_path, 'r') as f:
    text_format.Merge(f.read(), model_config)

  input_config = input_reader_pb2.InputReader()
  if FLAGS.eval_training_data:
    input_config_path = os.path.join(FLAGS.checkpoint_dir, 'train_input.config')
  else:
    input_config_path = os.path.join(FLAGS.checkpoint_dir, 'eval_input.config')
  with tf.gfile.GFile(input_config_path, 'r') as f:
    text_format.Merge(f.read(), input_config)

  return model_config, eval_config, input_config

def eval_main(max_number_of_evaluations=None):
  if FLAGS.eval_label:
    if FLAGS.pipeline_config_path == '':
      FLAGS.pipeline_config_path = '../configs/test/' + FLAGS.eval_label + '.config'
    if FLAGS.checkpoint_dir == '':
      FLAGS.checkpoint_dir = '../checkpoints/train/' + FLAGS.eval_label
    FLAGS.eval_dir = '../checkpoints/eval/' + FLAGS.eval_label
    FLAGS.eval_tag = FLAGS.eval_label

  assert FLAGS.checkpoint_dir, '`checkpoint_dir` is missing.'

  if FLAGS.pipeline_config_path:
    model_config, eval_config, input_config = get_configs_from_pipeline_file()
  elif FLAGS.eval_config_path:
    model_config, eval_config, input_config = get_configs_from_multiple_files()
  else:
    model_config, eval_config, input_config = get_configs_from_checkpoint_dir()

  if not FLAGS.eval_dir:
    if not FLAGS.eval_tag:
      FLAGS.eval_tag = time.strftime("%Y%m%d-%H%M%S")
    FLAGS.eval_dir = utils.mkdir_p(FLAGS.checkpoint_dir + '_eval_' + FLAGS.eval_tag)

  model_fn = functools.partial(
      model_builder.build,
      model_config=model_config,
      is_training=False)

  create_input_dict_fn = functools.partial(
      input_reader_builder.build,
      input_config)

  input_path = input_config.tf_record_input_reader.input_path
  num_examples = sum(1 for _ in tf.python_io.tf_record_iterator(input_path))

  label_map = label_map_util.load_labelmap(input_config.label_map_path)
  max_num_classes = max([item.id for item in label_map.item])
  categories = label_map_util.convert_label_map_to_categories(
      label_map, max_num_classes)

  evaluator.evaluate(
    create_input_dict_fn, model_fn, eval_config, categories,
    FLAGS.checkpoint_dir, FLAGS.eval_dir, num_examples,
    FLAGS.gpu_fraction, max_number_of_evaluations)

def main(unused_argv):
  eval_label_list = FLAGS.eval_label.split(',')
  if not FLAGS.eval_label or len(eval_label_list)==1:
    eval_main()
  else:
    while True:
      for eval_label in eval_label_list:
        FLAGS.eval_label = eval_label
        FLAGS.pipeline_config_path = ''
        FLAGS.checkpoint_dir = ''
        tf.reset_default_graph()
        eval_main(max_number_of_evaluations=1)
      break

if __name__ == '__main__':
  tf.app.run()
