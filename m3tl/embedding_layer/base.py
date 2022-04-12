# AUTOGENERATED! DO NOT EDIT! File to edit: source_nbs/18-00_embedding_layer.ipynb (unless otherwise specified).

__all__ = ['DefaultMultimodalEmbedding', 'DuplicateAugMultimodalEmbedding']

# Cell
import json
from typing import Dict
from collections import namedtuple

import tensorflow as tf
from loguru import logger
from ..base_params import BaseParams
from ..utils import get_shape_list

# Cell


class DefaultMultimodalEmbedding(tf.keras.Model):
    def __init__(self, params: BaseParams, embedding_layer: tf.keras.layers.Embedding = None):
        super(DefaultMultimodalEmbedding, self).__init__()
        self.params = params
        self.embedding_layer = embedding_layer

        self.embedding_dim = tf.shape(self.embedding_layer.weights[0])[1]
        if hasattr(self.embedding_dim, 'numpy'):
            self.embedding_dim = self.embedding_dim.numpy()

        # create modal type dict
        all_problem_info = self.params.get_problem_info()
        info_dict = {}
        [info_dict.update(d) for d in all_problem_info.values()]
        # create modal_name: modal_type dict
        self.modal_dict = {k.replace(
            '_modal_type', ''): v for k, v in info_dict.items() if '_modal_type' in k}
        # put text in front in order to be compatible with old version
        modal_tuple_list_for_sort = [
            (modal_name, modal_type, 0) if modal_type == 'text' else (
                modal_name, modal_type, 1)
            for modal_name, modal_type in self.modal_dict.items()
        ]
        self.ordered_modal_tuple_list = [
            (modal_name, modal_type) for modal_name, modal_type, _ in
            sorted(modal_tuple_list_for_sort, key=lambda x: x[-1])
        ]
        if not self.ordered_modal_tuple_list:
            raise ValueError(
                "Modal list is empty while creating embedding layer. It's"
                " most likely because you built the model before dataset is"
                " created or the current model path is different from the one when "
                "creating TFRecord. Since the number of modals is not certain before "
                "we see any data, an error is raised here. To resolve this, "
                "please call m3tl.input_fn.train_eval_input_fn(params) before"
                "the model is built or copy files from previous model path.")
        # create modal type ids
        self.modal_type_id = {k: i for i, k in enumerate(
            sorted(self.modal_dict.keys()))}
        logger.critical('Modal Type id mapping: \n {}'.format(
            json.dumps(self.modal_type_id, indent=4)))

        # create embedding layer for categorycal modal
        self.cate_embedding = {}
        for modal_name, modal_type in self.modal_dict.items():
            if modal_type == 'category':
                modal_info_name = '{}_modal_info'.format(modal_name)
                if modal_info_name not in info_dict:
                    raise ValueError(
                        'category modal {} dose not have modal '
                        'info, expect key: {}, receive keys: {}'.format(
                            modal_name, modal_info_name, info_dict.keys()))
                self.cate_embedding[modal_name] = tf.keras.layers.Embedding(
                    input_dim=info_dict[modal_info_name], output_dim=self.embedding_dim)

        # create dense layer for converting dimension for array modal
        self.multimodal_dense = {modal_name: tf.keras.layers.Dense(
            self.embedding_dim) for modal_name, modal_type in self.modal_dict.items()
            if modal_type == 'array'}
        # multimodal modal type embedding
        # this might raise no gradients warning if it's unimodal
        # variable: [3, 768]
        if self.params.enable_modal_type:
            self.modal_type_embedding = tf.keras.layers.Embedding(input_dim=len(
                self.modal_dict)+1, output_dim=self.embedding_dim)

        self.enable_modal_type = self.params.enable_modal_type

        # add modal sep weight
        self.sep_embedding = self.add_weight(name='modal_sep_embedding', shape=(
            1, 1, self.embedding_dim), dtype=tf.float32)

        self.dropout = tf.keras.layers.Dropout(self.params.dropout)

    @tf.function
    def call(self, inputs, training: bool = True):
        features_dict = inputs
        res_modal_input = tf.zeros(shape=(1, 1, 1))
        res_segment_ids = tf.zeros(shape=(1, 1))
        res_input_mask = tf.zeros(shape=(1, 1))
        modal_type_ids = tf.zeros(shape=(1, 1))
        for modal_idx, (modal_name, modal_type) in enumerate(self.ordered_modal_tuple_list):
            tf.autograph.experimental.set_loop_options(
                shape_invariants=[(res_modal_input, tf.TensorShape([None, None, None])),
                                  (res_segment_ids,
                                   tf.TensorShape([None, None])),
                                  (res_input_mask, tf.TensorShape(
                                      [None, None])),
                                  (modal_type_ids,
                                   tf.TensorShape([None, None]))
                                  ])

            input_ids = features_dict['{}_input_ids'.format(modal_name)]
            input_mask = features_dict['{}_mask'.format(modal_name)]
            segment_ids = features_dict['{}_segment_ids'.format(modal_name)]

            sep_embedding = tf.tile(self.sep_embedding, [
                                    tf.shape(input_ids)[0], 1, 1])

            if modal_type == 'text':
                input_shape = get_shape_list(input_ids)
                batch_size = input_shape[0]
                seq_length = input_shape[1]
                if input_mask is None:
                    input_mask = tf.ones(
                        shape=[batch_size, seq_length], dtype=tf.int32)

                if segment_ids is None:
                    segment_ids = tf.zeros(
                        shape=[batch_size, seq_length], dtype=tf.int32)

                modal_input = self.embedding_layer(input_ids)

            elif modal_type == 'array':

                if not self.enable_modal_type:
                    logger.warning('Seems there\'s a multimodal inputs but params.enable_modal_type is '
                                   'not set to be True.')

                # convert other modal embeddings to hidden_size
                # [batch_size, seq_length, modal_dim] -> [batch_size, seq_length, hidden_size]
                modal_input = self.multimodal_dense[modal_name](
                    input_ids)
            elif modal_type == 'category':
                modal_input = self.cate_embedding[modal_name](input_ids)

            # add sep embedding
            modal_input = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [modal_input, sep_embedding], axis=1)
            # add same type id to left and right
            modal_segment_ids = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [segment_ids,
                 tf.expand_dims(segment_ids[:, 0], axis=1)], axis=1)
            # add mask
            modal_mask = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [input_mask,
                    tf.expand_dims(input_mask[:, 0], axis=1)], axis=1)
            this_modal_type_ids = tf.ones_like(
                modal_segment_ids) * self.modal_type_id[modal_name]

            if modal_idx == 0:
                res_modal_input = modal_input
                res_segment_ids = modal_segment_ids
                res_input_mask = modal_mask
                modal_type_ids = this_modal_type_ids
            else:
                # concat correspondingly
                res_modal_input = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                    [res_modal_input, modal_input], axis=1)
                res_segment_ids = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                    [res_segment_ids, modal_segment_ids], axis=1)
                res_input_mask = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                    [res_input_mask, modal_mask], axis=1)
                if self.enable_modal_type:
                    modal_type_ids = tf.concat(
                        [modal_type_ids, this_modal_type_ids], axis=1)

        word_embedding = res_modal_input
        if self.enable_modal_type:
            word_embedding = word_embedding + \
                self.modal_type_embedding(modal_type_ids)

        # apply dropout
        word_embedding = self.dropout(word_embedding, training=training)
        EmbeddingHidden = namedtuple(
            'EmbeddingHidden', ['word_embedding', 'res_input_mask', 'res_segment_ids'])
        hidden_feature = EmbeddingHidden(
            word_embedding=word_embedding, res_input_mask=res_input_mask, res_segment_ids=res_segment_ids)

        return inputs, hidden_feature


# Cell


class DuplicateAugMultimodalEmbedding(DefaultMultimodalEmbedding):
    """
    This is majorly for SimCSE and also is a show case of how to
    implement in-batch data augmentation
    """
    @tf.function
    def call(self, inputs: Dict[str, tf.Tensor], training: bool=True):
        # simply copy every tensor and tile on batch_size
        # dimension except for loss multiplier
        if not self.params.duplicate_data_aug_problems:
            logger.warning(
                'DuplicateAugMultimodalEmbedding is specified as data augmentation strategy'
                ' but params.duplicate_data_aug_problems not set. This augmentation will be IGNORED.')
            return DefaultMultimodalEmbedding.call(self, inputs, training)

        # get problems that needs to be marked 1
        if isinstance(self.params.duplicate_data_aug_problems, str):
            dup_data_aug_problems = [
                self.params.duplicate_data_aug_problems]
        else:
            dup_data_aug_problems = self.params.duplicate_data_aug_problems

        loss_multiplier_suffix = '_loss_multiplier'
        dup_data_aug_loss_multiplier_name = [
            '{}{}'.format(p, loss_multiplier_suffix) for p in dup_data_aug_problems]

        dup_inputs = {}
        for tensor_keys in inputs.keys():
            # loss multiplier of duplicate data is 0 by default
            if tensor_keys.endswith(loss_multiplier_suffix) and \
                    (tensor_keys not in dup_data_aug_loss_multiplier_name):
                dup_inputs[tensor_keys] = tf.concat(
                    [inputs[tensor_keys], tf.zeros_like(inputs[tensor_keys])], axis=0)
            else:
                # repeat tensor
                dup_inputs[tensor_keys] = tf.concat(
                    [inputs[tensor_keys], inputs[tensor_keys]], axis=0)

        # just copy logic above
        # TODO: fix this bad approach
        features_dict = dup_inputs
        res_modal_input = tf.zeros(shape=(1, 1, 1))
        res_segment_ids = tf.zeros(shape=(1, 1))
        res_input_mask = tf.zeros(shape=(1, 1))
        modal_type_ids = tf.zeros(shape=(1, 1))
        for modal_idx, (modal_name, modal_type) in enumerate(self.ordered_modal_tuple_list):
            tf.autograph.experimental.set_loop_options(
                shape_invariants=[(res_modal_input, tf.TensorShape([None, None, None])),
                                  (res_segment_ids,
                                   tf.TensorShape([None, None])),
                                  (res_input_mask, tf.TensorShape(
                                      [None, None])),
                                  (modal_type_ids,
                                   tf.TensorShape([None, None]))
                                  ])

            input_ids = features_dict['{}_input_ids'.format(modal_name)]
            input_mask = features_dict['{}_mask'.format(modal_name)]
            segment_ids = features_dict['{}_segment_ids'.format(modal_name)]

            sep_embedding = tf.tile(self.sep_embedding, [
                                    tf.shape(input_ids)[0], 1, 1])

            if modal_type == 'text':
                input_shape = get_shape_list(input_ids)
                batch_size = input_shape[0]
                seq_length = input_shape[1]
                if input_mask is None:
                    input_mask = tf.ones(
                        shape=[batch_size, seq_length], dtype=tf.int32)

                if segment_ids is None:
                    segment_ids = tf.zeros(
                        shape=[batch_size, seq_length], dtype=tf.int32)

                modal_input = self.embedding_layer(input_ids)

            elif modal_type == 'array':

                if not self.enable_modal_type:
                    logger.warning('Seems there\'s a multimodal inputs but params.enable_modal_type is '
                                   'not set to be True.')

                # convert other modal embeddings to hidden_size
                # [batch_size, seq_length, modal_dim] -> [batch_size, seq_length, hidden_size]
                modal_input = self.multimodal_dense[modal_name](
                    input_ids)
            elif modal_type == 'category':
                modal_input = self.cate_embedding[modal_name](input_ids)

            # add sep embedding
            modal_input = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [modal_input, sep_embedding], axis=1)
            # add same type id to left and right
            modal_segment_ids = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [segment_ids,
                 tf.expand_dims(segment_ids[:, 0], axis=1)], axis=1)
            # add mask
            modal_mask = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [input_mask,
                    tf.expand_dims(input_mask[:, 0], axis=1)], axis=1)
            this_modal_type_ids = tf.ones_like(
                modal_segment_ids) * self.modal_type_id[modal_name]

            if modal_idx == 0:
                res_modal_input = modal_input
                res_segment_ids = modal_segment_ids
                res_input_mask = modal_mask
                modal_type_ids = this_modal_type_ids
            else:
                # concat correspondingly
                res_modal_input = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                    [res_modal_input, modal_input], axis=1)
                res_segment_ids = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                    [res_segment_ids, modal_segment_ids], axis=1)
                res_input_mask = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                    [res_input_mask, modal_mask], axis=1)
                if self.enable_modal_type:
                    modal_type_ids = tf.concat(
                        [modal_type_ids, this_modal_type_ids], axis=1)

        word_embedding = res_modal_input
        if self.enable_modal_type:
            word_embedding = word_embedding + \
                self.modal_type_embedding(modal_type_ids)

        # apply dropout
        word_embedding = self.dropout(word_embedding, training=training)
        EmbeddingHidden = namedtuple(
            'EmbeddingHidden', ['word_embedding', 'res_input_mask', 'res_segment_ids'])
        hidden_feature = EmbeddingHidden(
            word_embedding=word_embedding, res_input_mask=res_input_mask, res_segment_ids=res_segment_ids)

        return dup_inputs, hidden_feature