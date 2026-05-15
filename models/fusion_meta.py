"""Shared types for the fusion meta-model — kept in models/ so pickling works.

The trained model is saved to models/fusion_meta_v1.pkl via
scripts/train_fusion_meta_model.py.  At inference time, any script that
unpickles that file must import from this module (or import models) to make
ScaledLR resolvable.
"""
from __future__ import annotations

import numpy as np


class ScaledLR:
    """Logistic regression wrapped with a StandardScaler.

    Used as the fallback classifier when n < 80 samples — gradient boosting
    overfits severely at very low sample counts. At inference time, just call
    ``predict_proba(X)`` exactly as you would with any sklearn estimator.
    """

    def __init__(self, scaler, clf):
        self.scaler = scaler
        self.clf = clf

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(self.scaler.transform(X))
