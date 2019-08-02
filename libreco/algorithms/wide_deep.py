"""

Reference: Heng-Tze Cheng et al. "Wide & Deep Learning for Recommender Systems"  (https://arxiv.org/pdf/1606.07792.pdf)

author: massquantity

"""
import time, os
import itertools
from operator import itemgetter
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow import feature_column as feat_col
from tensorflow.python.estimator import estimator
from ..evaluate.evaluate import precision_tf, MAP_at_k, MAR_at_k, HitRatio_at_k, NDCG_at_k, NDCG_at_k_wd


class WideDeep:
    def __init__(self, embed_size, n_epochs=20, reg=0.0, cross_features=False,
                 batch_size=64, dropout=0.0, seed=42, task="rating"):
        self.embed_size = embed_size
        self.n_epochs = n_epochs
        self.reg = reg
        self.batch_size = batch_size
        self.dropout = dropout
        self.seed = seed
        self.task = task
        self.cross_features = cross_features

    def build_model(self, dataset):
        wide_cols = []
        deep_cols = []
        if self.cross_features:
            for i in range(len(dataset.feature_cols)):
                for j in range(i + 1, len(dataset.feature_cols)):
                    col1 = dataset.feature_cols[i]
                    col2 = dataset.feature_cols[j]
                    col1_feat = feat_col.categorical_column_with_vocabulary_list(
                        col1, dataset.col_unique_values[col1])
                    col2_feat = feat_col.categorical_column_with_vocabulary_list(
                        col2, dataset.col_unique_values[col2])
                    if col1 not in ["user", "item"] and col2 not in ["user", "item"]:
                        wide_cols.append(feat_col.crossed_column([col1_feat, col2_feat], hash_bucket_size=1000))
                    elif col1 in ["user", "item"]:
                        wide_cols.append(col1_feat)

            for col in dataset.feature_cols:
                col_feat = feat_col.categorical_column_with_vocabulary_list(col, dataset.col_unique_values[col])
                if len(dataset.col_unique_values[col]) < 10:
                    deep_cols.append(feat_col.indicator_column(col_feat))
                else:
                    deep_cols.append(feat_col.embedding_column(col_feat, dimension=self.embed_size))

        else:
            for col in dataset.feature_cols:
                col_feat = feat_col.categorical_column_with_vocabulary_list(col, dataset.col_unique_values[col])
                wide_cols.append(col_feat)
                if len(dataset.col_unique_values[col]) < 10:
                    deep_cols.append(feat_col.indicator_column(col_feat))
                else:
                    deep_cols.append(feat_col.embedding_column(col_feat, dimension=self.embed_size))


        config = tf.estimator.RunConfig(log_step_count_steps=1000, save_checkpoints_steps=10000)
        if self.task == "rating":
            self.model = tf.estimator.DNNLinearCombinedRegressor(
                model_dir="wide_deep_dir",
                config=config,
                linear_feature_columns=wide_cols,
                linear_optimizer=tf.train.FtrlOptimizer(learning_rate=0.1, l1_regularization_strength=0.001),
                dnn_feature_columns=deep_cols,
                dnn_hidden_units=[128, 64],
                dnn_optimizer=tf.train.AdamOptimizer(learning_rate=0.01),
                dnn_dropout=0.0,
                batch_norm=False,
                loss_reduction=tf.losses.Reduction.MEAN)

        elif self.task == "ranking":
            self.model = tf.estimator.DNNLinearCombinedClassifier(
                model_dir="wide_deep_dir",
                config=config,
                linear_feature_columns=wide_cols,
                linear_optimizer=tf.train.FtrlOptimizer(learning_rate=0.1),  # l1_regularization_strength=0.001
                dnn_feature_columns=deep_cols,
                dnn_hidden_units=[128, 64],
                dnn_optimizer=tf.train.AdamOptimizer(learning_rate=0.01),
                dnn_dropout=0.0,
                batch_norm=False,
                loss_reduction=tf.losses.Reduction.MEAN)

        else:
            raise ValueError("task must be rating or ranking")

    @staticmethod
    def input_fn(data, original_data=None, repeat=10, batch=256, mode="train", task="rating", user=None):
        if mode == "train":
            features = {col: data.train_data[col].values for col in data.feature_cols}
            labels = data.train_data[data.label_cols].values

            train_data = tf.data.Dataset.from_tensor_slices((features, labels))
            return train_data.shuffle(len(labels)).repeat(repeat).batch(batch)

        elif mode == "evaluate":
            features = {col: data.test_data[col].values.reshape(-1, 1) for col in data.feature_cols}
            labels = data.test_data[data.label_cols].values.reshape(-1, 1)

            evaluate_data = tf.data.Dataset.from_tensor_slices((features, labels))
            return evaluate_data

        elif mode == "test":
            features = {col: data.test_data[col].values for col in data.feature_cols}
            test_data = tf.data.Dataset.from_tensor_slices(features)
            return test_data

        elif mode == "rank":
            n_items = len(data.item_dict)
            user_part = pd.DataFrame([data.user_dict[user]], columns=data.user_feature_cols)
            user_part = user_part.reindex(user_part.index.repeat(n_items))
            item_part = pd.DataFrame(list(data.item_dict.values()), columns=data.item_feature_cols)
            features = {col: user_part[col].values for col in user_part.columns}
            features.update({col: item_part[col].values for col in item_part.columns})

            rank_data = tf.data.Dataset.from_tensor_slices(features)
            return rank_data

    def fit(self, dataset):
        self.dataset = dataset
        self.build_model(dataset)
        for epoch in range(1, 3):  # epoch_per_eval
            t0 = time.time()
            self.model.train(input_fn=lambda: WideDeep.input_fn(
                data=dataset, repeat=self.n_epochs, batch=self.batch_size, mode="train", task=self.task))
            train_loss = self.model.evaluate(input_fn=lambda: WideDeep.input_fn(
                data=dataset, repeat=self.n_epochs, batch=self.batch_size, mode="train", task=self.task))
            evaluate_loss = self.model.evaluate(input_fn=lambda: WideDeep.input_fn(
                data=dataset, repeat=self.n_epochs, batch=self.batch_size, mode="evaluate", task=self.task))
            print("Epoch {} training time: {:.4f}".format(epoch, time.time() - t0))
            print("train loss: {loss:.4f}, step: {global_step}".format(**train_loss))
            print("evaluate loss: {loss:.4f}".format(**evaluate_loss))

    def predict(self, u, i, *args):
        pred_result = self.model.predict(input_fn=lambda: WideDeep.input_fn(
            data=[u, i, args], original_data=self.dataset, mode="test"))
        if self.task == "rating":
            return list(pred_result)[0]['predictions']
        elif self.task == "ranking":
            return list(pred_result)[0]['class_ids']

    def predict_user(self, u):
        rank_list = self.model.predict(input_fn=lambda: WideDeep.input_fn(
            data=self.dataset, original_data=self.dataset, mode="rank", user=u))
        if self.task == "rating":
            return sorted([(item, rating['predictions'][0]) for item, rating in enumerate(list(rank_list))],
                          key=itemgetter(1), reverse=True)[:10]
        elif self.task == "ranking":
            return sorted([(item, rating['probabilities'][0]) for item, rating in enumerate(list(rank_list))],
                          key=itemgetter(1), reverse=True)[:10]



# TODO batch_norm, dropout
class WideDeepCustom(estimator.Estimator):  # tf.estimator.Estimator,  NOOOO inheritance
    def __init__(self, embed_size, n_epochs=20, reg=0.0, cross_features=False,
                 batch_size=64, dropout=0.0, seed=42, task="rating"):
        self.embed_size = embed_size
        self.n_epochs = n_epochs
        self.reg = reg
        self.batch_size = batch_size
        self.dropout = dropout
        self.seed = seed
        self.task = task
        self.cross_features = cross_features
        super(WideDeepCustom, self).__init__(model_fn=WideDeepCustom.model_func)

    def build_model(self, dataset):
        wide_cols = []
        deep_cols = []
        if self.cross_features:
            for i in range(len(dataset.feature_cols)):
                for j in range(i + 1, len(dataset.feature_cols)):
                    col1 = dataset.feature_cols[i]
                    col2 = dataset.feature_cols[j]
                    col1_feat = feat_col.categorical_column_with_vocabulary_list(
                        col1, dataset.col_unique_values[col1])
                    col2_feat = feat_col.categorical_column_with_vocabulary_list(
                        col2, dataset.col_unique_values[col2])
                    if (col1 not in ["user", "item", "title"]) and (col2 not in ["user", "item", "title"]):
                        wide_cols.append(feat_col.crossed_column([col1_feat, col2_feat], hash_bucket_size=1000))

            for col in dataset.feature_cols:
                col_feat = feat_col.categorical_column_with_vocabulary_list(col, dataset.col_unique_values[col])
                if col in ["user", "item", "title"]:
                    wide_cols.append(col_feat)
                    deep_cols.append(feat_col.embedding_column(col_feat, dimension=self.embed_size))
                elif len(dataset.col_unique_values[col]) < 10:
                    deep_cols.append(feat_col.indicator_column(col_feat))
                else:
                    deep_cols.append(feat_col.embedding_column(col_feat, dimension=self.embed_size))

        else:
            for col in dataset.feature_cols:
                col_feat = feat_col.categorical_column_with_vocabulary_list(col, dataset.col_unique_values[col])
                wide_cols.append(col_feat)
                if len(dataset.col_unique_values[col]) < 10:
                    deep_cols.append(feat_col.indicator_column(col_feat))
                else:
                    deep_cols.append(feat_col.embedding_column(col_feat, dimension=self.embed_size))

        config = tf.estimator.RunConfig(log_step_count_steps=100000, save_checkpoints_steps=100000)
        model = tf.estimator.Estimator(model_fn=WideDeepCustom.model_func,
                                       model_dir="wide_deep_dir",
                                       config=config,
                                       params={'deep_columns': deep_cols,
                                               'wide_columns': wide_cols,
                                               'hidden_units': [128, 64],
                                               'task': self.task})

        return model

    @staticmethod
    def model_func(features, labels, mode, params):
        dnn_input = feat_col.input_layer(features, params['deep_columns'])
        for units in params['hidden_units']:
            dnn_input = tf.layers.dense(dnn_input, units=units, activation=tf.nn.relu)
        dnn_logits = tf.layers.dense(dnn_input, units=10, activation=None)

        linear_logits = feat_col.linear_model(units=10, features=features,
                                              feature_columns=params['wide_columns'])

    #    logits = tf.add_n([dnn_logits, linear_logits])
        concat = tf.concat([dnn_logits, linear_logits], axis=-1)
        logits = tf.layers.dense(concat, units=1)
        if params['task'] == "rating":
            if mode == tf.estimator.ModeKeys.PREDICT:
                predictions = {'predictions': logits}
                return tf.estimator.EstimatorSpec(mode, predictions=predictions)

            labels = tf.cast(tf.reshape(labels, [-1, 1]), tf.float32)
            loss = tf.losses.mean_squared_error(labels=labels, predictions=logits)
            rmse = tf.sqrt(tf.losses.mean_squared_error(labels=labels, predictions=tf.clip_by_value(logits, 1, 5)))
            metrics = {'rmse': rmse}

        elif params['task'] == "ranking":
            y_prob = tf.sigmoid(logits)
            pred = tf.where(y_prob >= 0.5,
                            tf.fill(tf.shape(y_prob), 1.0),
                            tf.fill(tf.shape(y_prob), 0.0))

            if mode == tf.estimator.ModeKeys.PREDICT:
                predictions = {'class_ids': pred,
                               'probabilities': y_prob,
                               'logits': logits}
            #    predictions = y_prob[0]
                return tf.estimator.EstimatorSpec(mode, predictions=predictions)

            labels = tf.cast(tf.reshape(labels, [-1, 1]), tf.float32)
            loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits))
            #    labels = tf.cast(labels, tf.int64)
            accuracy = tf.metrics.accuracy(labels=labels, predictions=pred)
            #    precision_at_k = tf.metrics.precision_at_k(labels=labels, predictions=logits, k=10)
            #    precision_at_k_2 = tf.metrics.precision_at_top_k(labels=labels, predictions_idx=pred, k=10)
            precision = tf.metrics.precision(labels=labels, predictions=pred)
            recall = tf.metrics.recall(labels=labels, predictions=pred)
            f1 = tf.contrib.metrics.f1_score(labels=labels, predictions=pred)
            auc_roc = tf.metrics.auc(labels=labels, predictions=pred, curve="ROC",
                                     summation_method='careful_interpolation')
            auc_pr = tf.metrics.auc(labels=labels, predictions=pred, curve="PR",
                                    summation_method='careful_interpolation')
            metrics = {'accuracy': accuracy, 'precision': precision, 'recall': recall, 'f1': f1,
                       'auc_roc': auc_roc, 'auc_pr': auc_pr}

        if mode == tf.estimator.ModeKeys.EVAL:
            return tf.estimator.EstimatorSpec(mode, loss=loss, eval_metric_ops=metrics)

        assert mode == tf.estimator.ModeKeys.TRAIN

        '''
        train_op = []
        optimizer2 = tf.train.AdamOptimizer(learning_rate=0.01)
        training_op2 = optimizer2.minimize(loss, global_step=tf.train.get_global_step())
        train_op.append(training_op2)
        optimizer = tf.train.FtrlOptimizer(learning_rate=0.1, l1_regularization_strength=1e-3) ### Adagrad
        training_op = optimizer.minimize(loss, global_step=tf.train.get_global_step())
        train_op.append(training_op)
        return tf.estimator.EstimatorSpec(mode, loss=loss, train_op=tf.group(*train_op))
        '''
    #    optimizer = tf.train.FtrlOptimizer(learning_rate=0.1, l1_regularization_strength=1e-3)
        optimizer = tf.train.AdamOptimizer(learning_rate=0.007)
    #    optimizer = tf.train.AdagradOptimizer(learning_rate=0.01)
    #    optimizer = tf.train.ProximalAdagradOptimizer(learning_rate=0.01)
        training_op = optimizer.minimize(loss, global_step=tf.train.get_global_step())
        return tf.estimator.EstimatorSpec(mode, loss=loss, train_op=training_op)

    @staticmethod
    def input_fn(data, repeat=1, batch=256, mode="train", user=None, item=None):  # , original_data=None
        if mode == "train":
            features = {col: data.train_data[col].values for col in data.feature_cols}
        #    features = {"age": data.train_data["age"].values}
            labels = data.train_data[data.label_cols].values

            train_data = tf.data.Dataset.from_tensor_slices((features, labels))
            return train_data.shuffle(len(labels)).repeat(repeat).batch(batch)

        elif mode == "evaluate":
            features = {col: data.test_data[col].values.reshape(-1, 1) for col in data.feature_cols}
            labels = data.test_data[data.label_cols].values.reshape(-1, 1)

            evaluate_data = tf.data.Dataset.from_tensor_slices((features, labels))
            return evaluate_data

        elif mode == "test":
            user_part = pd.DataFrame([data.user_dict[user]], columns=data.user_feature_cols)
            item_part = pd.DataFrame([data.item_dict[item]], columns=data.item_feature_cols)
            features = {col: user_part[col].values.reshape(-1, 1) for col in user_part.columns}
            features.update({col: item_part[col].values.reshape(-1, 1) for col in item_part.columns})
            for col, col_type in data.column_types:
                if col_type == np.float32 or col_type == np.float64:
                    features[col] = features[col].astype(int)
                elif col != "label":
                    features[col] = features[col].astype(col_type)

            test_data = tf.data.Dataset.from_tensor_slices(features)
            return test_data

        elif mode == "rank":
            n_items = len(data.item_dict)
            user_part = pd.DataFrame([data.user_dict[user]], columns=data.user_feature_cols)
            user_part = user_part.reindex(user_part.index.repeat(n_items))
            item_part = pd.DataFrame(list(data.item_dict.values()), columns=data.item_feature_cols)
            features = {col: user_part[col].values.reshape(-1, 1) for col in user_part.columns}
            features.update({col: item_part[col].values.reshape(-1, 1) for col in item_part.columns})
            for col, col_type in data.column_types:
                if col_type == np.float32 or col_type == np.float64:
                    features[col] = features[col].astype(int)
                elif col != "label":
                    features[col] = features[col].astype(col_type)
            rank_data = tf.data.Dataset.from_tensor_slices(features).batch(batch)
            return rank_data

    def fit(self, dataset, verbose=1):
        self.dataset = dataset
        self.model = self.build_model(dataset)
        for epoch in range(1, self.n_epochs):  # epoch_per_eval
            t0 = time.time()
            self.model.train(input_fn=lambda: WideDeepCustom.input_fn(
                data=dataset, repeat=1, batch=self.batch_size, mode="train"))
            train_result = self.model.evaluate(input_fn=lambda: WideDeepCustom.input_fn(
                data=dataset, repeat=1, batch=self.batch_size, mode="train"))
            eval_result = self.model.evaluate(input_fn=lambda: WideDeepCustom.input_fn(
                data=dataset, mode="evaluate"))

            if verbose > 0:
                print("Epoch {} training time: {:.4f}".format(epoch, time.time() - t0))
                if self.task == "rating":
                    print("train loss: {loss:.4f}, train rmse: {rmse:.4f}".format(**train_result))
                    print("test loss: {loss:.4f}, test rmse: {rmse:.4f}".format(**eval_result))
                elif self.task == "ranking":
                    print("train loss: {loss:.4f}, accuracy: {accuracy:.4f}, precision: {precision:.4f}, "
                          "recall: {recall:.4f}, f1: {f1:.4f}, auc_roc: {auc_roc:.4f}, "
                          "auc_pr: {auc_pr:.4f}".format(**train_result))
                    print("test loss: {loss:.4f}, accuracy: {accuracy:.4f}, precision: {precision:.4f}, "
                          "recall: {recall:.4f}, f1: {f1:.4f}, auc_roc: {auc_roc:.4f}, "
                          "auc_pr: {auc_pr:.4f}".format(**eval_result))

    #    t0 = time.time()
    #    NDCG = NDCG_at_k(self, self.dataset, 10, mode="wide_deep")
    #    print("\t NDCG @ {}: {:.4f}".format(10, NDCG))
    #    print("\t NDCG time: {:.4f}".format(time.time() - t0))

    def predict_ui(self, u, i):  # cannot override Estimator's predict method
        pred_result = self.model.predict(input_fn=lambda: WideDeepCustom.input_fn(
            data=self.dataset, mode="test", user=u, item=i))  #  original_data=self.dataset
        pred_result = list(pred_result)[0]
        if self.task == "rating":
            return list(pred_result)[0]['predictions']
        elif self.task == "ranking":
            return pred_result['probabilities'][0], pred_result['class_ids'][0]

    def recommend_user(self, u, n_rec=10):
        rank_list = self.model.predict(input_fn=lambda: WideDeepCustom.input_fn(
            data=self.dataset, mode="rank", user=u, batch=256))

        items = np.array(list(self.dataset.item_dict.keys()))
        if self.task == "rating":
            rank = np.array([res['predictions'][0] for res in rank_list])
            indices = np.argpartition(rank, -n_rec)[-n_rec:]
            return sorted(zip(items[indices], rank[indices]), key=lambda x: -x[1])

        elif self.task == "ranking":
            rank = np.array([res['probabilities'][0] for res in rank_list])
            indices = np.argpartition(rank, -n_rec)[-n_rec:]
            return sorted(zip(items[indices], rank[indices]), key=itemgetter(1), reverse=True)







