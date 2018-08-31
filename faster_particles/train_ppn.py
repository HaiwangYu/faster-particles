# *-* encoding: utf-8 *-*
# Trainer class for PPN, base network and small UResNet

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
import os
import numpy as np

from faster_particles.ppn import PPN
from faster_particles.demo_ppn import load_weights, get_data
from faster_particles.display_utils import display, display_uresnet, \
                                            display_ppn_uresnet
from faster_particles.base_net import basenets
from faster_particles.cropping import cropping_algorithms


class Trainer(object):
    def __init__(self, net, train_toydata, test_toydata,
                 cfg, display_util=None):
        self.train_toydata = train_toydata
        self.test_toydata = test_toydata
        self.net = net
        self.display = display_util
        self.cfg = cfg
        self.dim = 3 if self.cfg.DATA_3D else 2

        self.logdir = cfg.LOG_DIR
        self.displaydir = cfg.DISPLAY_DIR
        self.outputdir = cfg.OUTPUT_DIR
        if not os.path.isdir(self.logdir):
            os.makedirs(self.logdir)
        if not os.path.isdir(self.displaydir):
            os.makedirs(self.displaydir)
        if not os.path.isdir(self.outputdir):
            os.makedirs(self.outputdir)

    def process_blob(self, i, blob, real_step, saver, is_testing,
                     summary_writer_train, summary_writer_test):
        real_step += 1
        is_drawing = real_step % 1000 == 0

        if real_step % 100 == 0:
            print("(Real) Step %d" % real_step)

        if self.cfg.NET == 'small_uresnet':
            blob['data'] = np.reshape(
                blob['crops'],
                (-1,) + (self.cfg.CROP_SIZE,) * self.dim + (1,))
            blob['labels'] = np.reshape(
                blob['crops_labels'],
                (-1,) + (self.cfg.CROP_SIZE,) * self.dim)
            if is_testing:
                summary, result = self.test_net.test_image(self.sess, blob)
                summary_writer_test.add_summary(summary, real_step)
            else:
                summary, result = self.train_net.train_step_with_summary(self.sess, blob)
                summary_writer_train.add_summary(summary, real_step)
            for i in range(len(blob['crops'])):
                blob_i = {
                    'data': np.reshape(
                        blob['crops'][i],
                        (1,) + (self.cfg.CROP_SIZE,) * self.dim + (1,)
                        ),
                    'labels': np.reshape(
                        blob['crops_labels'][i],
                        (1,) + (self.cfg.CROP_SIZE,) * self.dim
                        )
                    }
                if is_drawing and self.display is not None:
                    N = self.cfg.IMAGE_SIZE
                    self.cfg.IMAGE_SIZE = self.cfg.CROP_SIZE
                    self.display(blob_i,
                                 self.cfg,
                                 index=real_step,
                                 name='display_train',
                                 directory=os.path.join(
                                     self.cfg.DISPLAY_DIR,
                                     'train'),
                                 vmin=0,
                                 vmax=1,
                                 predictions=np.reshape(
                                     result['predictions'][i],
                                     (1,) + (self.cfg.CROP_SIZE,) * self.dim
                                     )
                                 )
                    self.cfg.IMAGE_SIZE = N
        else:
            if is_testing:
                summary, result = self.test_net.test_image(self.sess, blob)
                summary_writer_test.add_summary(summary, real_step)
            else:
                summary, result = self.train_net.train_step_with_summary(self.sess, blob)
                summary_writer_train.add_summary(summary, real_step)
            # print(np.count_nonzero(blob['data']), np.count_nonzero(result['predictions']))
            if is_drawing and self.display is not None:
                print('Drawing...')
                if self.cfg.NET == 'ppn':
                    result['dim1'] = self.train_net.dim1
                    result['dim2'] = self.train_net.dim2
                if self.cfg.ENABLE_CROP:
                    N = self.cfg.IMAGE_SIZE
                    self.cfg.IMAGE_SIZE = self.cfg.SLICE_SIZE
                self.display(blob,
                             self.cfg,
                             index=real_step,
                             name='display_train',
                             directory=os.path.join(self.cfg.DISPLAY_DIR,
                                                    'train'),
                             **result)
                if self.cfg.ENABLE_CROP:
                    self.cfg.IMAGE_SIZE = N
                print("Done.")

        if real_step % 1000 == 0:
            save_path = saver.save(self.sess,
                                   os.path.join(self.outputdir,
                                                "model-%d.ckpt" % real_step))
            print("Wrote %s" % save_path)
        return real_step

    def train(self, net_args, scope="ppn"):
        print("Creating net architecture...")
        net_args['cfg'] = self.cfg
        self.train_net = self.net(**net_args)
        self.test_net = self.net(**net_args)
        self.test_net.restore_placeholder(self.train_net.init_placeholders())
        self.train_net.create_architecture(is_training=True,
                                           reuse=False,
                                           scope=scope)
        self.test_net.create_architecture(is_training=False,
                                          reuse=True,
                                          scope=scope)
        print("Done.")

        # with tf.Session() as sess:
        self.sess = tf.InteractiveSession()
        self.sess.run(tf.global_variables_initializer())
        load_weights(self.cfg, self.sess)
        summary_writer_train = tf.summary.FileWriter(
            os.path.join(self.logdir, 'train'), self.sess.graph)
        summary_writer_test = tf.summary.FileWriter(
            os.path.join(self.logdir, 'test'), self.sess.graph)

        # Global saver
        # saver = None
        # if self.cfg.FREEZE: # Save only PPN weights (excluding base network)
        #     variables_to_restore = [v for v in tf.global_variables() if "ppn" in v.name]
        #     saver = tf.train.Saver(variables_to_restore)
        # else: # Save everything (including base network)
        saver = tf.train.Saver()

        step = 0
        crop_algorithm = cropping_algorithms[self.cfg.CROP_ALGO](self.cfg)
        if self.cfg.ENABLE_CROP:
            self.batch_size = self.cfg.BATCH_SIZE
            self.cfg.BATCH_SIZE = 1

        print("Start training...")
        real_step = 0
        for step in range(self.cfg.MAX_STEPS):
            is_testing = step % 10 == 5
            if is_testing:
                blob = self.test_toydata.forward()
            else:
                blob = self.train_toydata.forward()

            # Cropping pre-processing
            if self.cfg.ENABLE_CROP:
                batch_blobs = crop_algorithm.process(blob)
            else:
                batch_blobs = [blob]

            i = 0
            while i < len(batch_blobs):
                blobs = batch_blobs[i:min(i+self.batch_size, len(batch_blobs))]
                blob = {}
                for key in blobs[0]:
                    blob[key] = np.concatenate([b[key] for b in blobs])

                i += len(blob)
                real_step = self.process_blob(i, blob, real_step, saver,
                                              is_testing, summary_writer_train,
                                              summary_writer_test)
        summary_writer_train.close()
        summary_writer_test.close()
        print("Done.")


def train_ppn(cfg):
    """
    Launch PPN training with appropriate dataset and base layers.
    """
    train_data, test_data = get_data(cfg)
    net_args = {"base_net": basenets[cfg.BASE_NET], "base_net_args": {}}
    display_util = display if cfg.NET == 'ppn' else display_ppn_uresnet
    t = Trainer(PPN, train_data, test_data, cfg, display_util=display_util)
    t.train(net_args)


def train_classification(cfg):
    """
    Launch training of the base network (e.g. VGG or UResNet).
    """
    train_data, test_data = get_data(cfg)
    net_args = {}
    display_util = display_uresnet if cfg.BASE_NET == 'uresnet' else None
    t = Trainer(basenets[cfg.BASE_NET],
                train_data,
                test_data,
                cfg,
                display_util=display_util)
    t.train(net_args, scope=cfg.BASE_NET)


def train_small_uresnet(cfg):
    """
    Launch training of the small UResNet.
    """
    train_data, test_data = get_data(cfg)
    t = Trainer(basenets[cfg.BASE_NET],
                train_data,
                test_data,
                cfg,
                display_util=display_uresnet)
    net_args = {"N": cfg.CROP_SIZE}
    t.train(net_args, scope="small_uresnet")
