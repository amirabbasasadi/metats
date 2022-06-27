import numpy as np
from metats.forecasters.utils import is_sktime_forecaster, is_darts_forecaster
from metats.forecasters.wrappers import DartsForecasterWrapper, SKTimeForecasterWrapper

class MetaLearning():
    """
    Base Class for Meta-Learning Pipelines
    """
    def __init__(self, method='averaging', loss='mse', metalearning_split=7):
        self.method = method
        self.feature_generators = []
        self.base_forecasters = []
        self.meta_learner = None
        self.loss = loss
        self.metalearning_split = metalearning_split
    
    def add_feature(self, feature_generator):
        """
        Add a feature generator to the pipeline
        """
        self.feature_generators.append(feature_generator)
    
    def add_forcecaster(self, forecaster):
        """
        Add a base forecaster to the pipeline
        """
        # checking for dart forecasters
        if is_darts_forecaster(forecaster):
            wrapped = DartsForecasterWrapper(forecaster)
            self.base_forecasters.append(wrapped)   
        elif is_sktime_forecaster(forecaster):
            wrapped = SKTimeForecasterWrapper(forecaster)
            self.base_forecasters.append(wrapped) 
        else:
            self.base_forecasters.append(forecaster)
    
    def loss_fn(self, y_true, y_pred):
        """
        Loss function for the meta-learner
        Args:
            y_true: true values (numpy array) (num_series x horizon)
            Y_pred: the matrix of predictions (num_series x num_models x horizon)
        """
        if self.loss == 'mse':
            return np.mean(np.square(y_true - y_pred), axis=2)
        elif self.loss == 'mae':
            return np.mean(np.abs(y_true - y_pred), axis=2)
        elif callable(self.loss):
            return self.loss(y_true, y_pred)
        else:
            raise ValueError('Loss function not supported')

    def labels_selection(self, y_true, Y_pred, return_one_hot=False):
        """
        Generate labels for the meta-learner using selection
        Args:
            y_true: true values (numpy array) (num_series x horizon)
            Y_pred: the matrix of predictions (num_series x num_models x horizon)
        """
        error = self.loss_fn(y_true, np.transpose(Y_pred, (1, 0, 2)))
        error = error.T
        label_indices = np.argmin(error, axis=1)
        if return_one_hot:    
            # convert to one-hot encoding
            labels = np.zeros(Y_pred.shape[1:])
            for i in range(Y_pred.shape[1]):
                labels[i, label_indices[i]] = 1.0
            return labels
        else:
            return label_indices

    def generate_labels(self, y_true, Y_pred):
        """
        Generate labels for the meta-learner
        Args:
            y_true: true values
            Y_pred: the matrix of predictions 
        """
        if self.method == 'selection':
            return self.labels_selection(y_true, Y_pred)
        elif self.method == 'averaging':
            return self.labels_selection(y_true, Y_pred)
        else:
            raise ValueError('Method not supported')
        
    def generate_features(self, Y):
        # generate meta features
        meta_features = []
        for feature_generator in self.feature_generators:
            meta_features.append(feature_generator.fit_transform(Y))
        return np.hstack(meta_features)
    
    def generate_prediction(self, forecaster, Y, fh, forecast_dim):
        """
        Generate predictions for a single forecaster
        Args:
            forecaster: the forecaster to be used
            Y: the timeseries (numpy array) (num_series x series_length x covariates_dim)
            fh: forecast horizon
            forecast_dim: the dimension of the variable to be forecasted
        """
        return forecaster.predict(Y, fh, forecast_dim)
    
    def generate_predictions(self, Y, fh, forecast_dim=0):
        # generate predictions
        predictions = []
        for base_forecaster in self.base_forecasters:
            prediction = self.generate_prediction(base_forecaster, Y, fh, forecast_dim) 
            if prediction.ndim == 3:
                prediction = prediction[:, :, forecast_dim]
            prediction = np.expand_dims(prediction, axis=1)
            predictions.append(prediction)
        predictions = np.concatenate(predictions, axis=1)
        return predictions
    
    def add_metalearner(self, metalearner):
        """
        Add a meta-learner to the pipeline
        Args:
            metalearner: the meta-learner to be added a scikit-learn estimator
        """
        self.meta_learner = metalearner

    def fit(self, Y, fh, forecast_dim=0):
        """
        Fit the meta-learner
        Args:
            Y: the timeseries (numpy array) (num_series x series_length x covariates_dim)
        """
        # data spliting for meta-learning
        Y_true = Y[:, -fh:, forecast_dim]

        self.features_fh = fh
        meta_features = self.generate_features(Y[:, :-fh, :])
        predictions = self.generate_predictions(Y[:, :-fh, :], fh, forecast_dim=forecast_dim)
        labels = self.generate_labels(Y_true, predictions)

        self.meta_learner.fit(X=meta_features, y=labels)

    def predict_generate_weights(self, meta_features):
        """
        Predict using the meta-learner
        Args:
            metafeatures: the extracted meta-features (numpy array) (num_series x features_dim)
        """
        weights = None
        if self.method == 'selection':
            weights = self.meta_learner.predict(meta_features)
        elif self.method == 'averaging':
            weights = self.meta_learner.predict_proba(meta_features)
        
        return weights

    def averaging_predictions(self, weights, predictions):
        """
        Generating the predictions by averaging each base-forecaster
        """
        weighted_predictions = []
        for series in range(predictions.shape[0]):
            p = weights[series, :].reshape(1, -1) @ predictions[series, :, :]
            weighted_predictions.append(p)
        result = np.vstack(weighted_predictions)
        return result
    
    def selection_predictions(self, weights, predictions):
        """
        Generating the predictions by selecting the best-forecaster
        """
        selected_predictions = []
        for series in range(predictions.shape[0]):
            p = predictions[series, weights[series], :].reshape(1,-1)
            selected_predictions.append(p)
        result = np.vstack(selected_predictions)
        return result

    def predict(self, Y, fh, forecast_dim=0, return_weights=False):
        """
        Predict using the meta-learner
        Args:
            Y: the timeseries (numpy array) (num_series x series_length x covariates_dim)
            fh: forecast horizon for predicting
            forecast_dim: the dimension of the variable to be forecasted
        """
        meta_features = self.generate_features(Y[:, :-self.features_fh, :])
        predictions = self.generate_predictions(Y, fh, forecast_dim=forecast_dim)
        weights = self.predict_generate_weights(meta_features)
        
        if self.method == 'averaging':
            result = self.averaging_predictions(weights, predictions)
        else:
            result = self.selection_predictions(weights, predictions)
        
        return result

