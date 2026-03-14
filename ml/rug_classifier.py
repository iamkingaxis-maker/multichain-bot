"""
ML Rug Pull Classifier
Trains a gradient boosting model on behavioral features
to predict rug probability before buying.

Catches sophisticated rugs that pass GoPlus checks by
analyzing behavioral patterns rather than just contract code.

Feature extraction:
  - Token age (minutes since creation)
  - Creator wallet history
  - Early buyer concentration
  - Buy/sell timing patterns
  - Liquidity provision behavior
  - Social link age and authenticity
  - Price action patterns in first 30 minutes

Model: Gradient Boosting (via scikit-learn)
  - Trained on labeled historical token data
  - Outputs probability 0.0-1.0 (higher = more likely rug)
  - Retrained automatically as bot collects more data

Data collection:
  - Every token the bot evaluates gets logged with features
  - Tokens that later rug get labeled automatically
  - Model retrains weekly on accumulated data

To bootstrap before enough data is collected:
  - Uses conservative heuristic rules as fallback
  - Transitions to ML model once 200+ labeled examples exist
"""

import asyncio
import json
import logging
import os
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import aiohttp

logger = logging.getLogger(__name__)

MODEL_FILE = "ml/rug_classifier.pkl"
TRAINING_DATA_FILE = "ml/training_data.json"
MIN_SAMPLES_FOR_ML = 200        # Need this many labeled examples before using ML
RUG_DETECTION_WINDOW_HOURS = 48  # A token is labeled rug if it drops 80%+ in 48h


@dataclass
class TokenFeatures:
    """Feature vector for a single token evaluation."""
    token_address: str
    chain_id: str
    timestamp: str

    # Token age features
    age_minutes: float = 0.0
    time_to_first_lp_minutes: float = 0.0

    # Creator/wallet features
    creator_wallet_age_days: float = 0.0
    creator_prev_tokens: int = 0
    creator_prev_rugs: int = 0
    creator_wallet_sol_balance: float = 0.0

    # Early buyer features
    buyers_first_30s: int = 0
    buyers_first_5min: int = 0
    unique_buyers_1h: int = 0
    top_holder_pct: float = 0.0
    top5_holders_pct: float = 0.0

    # Trading pattern features
    buy_sell_ratio_5min: float = 0.0
    buy_sell_ratio_30min: float = 0.0
    volume_first_5min_usd: float = 0.0
    price_change_5min: float = 0.0
    price_change_30min: float = 0.0
    volatility_30min: float = 0.0

    # Liquidity features
    lp_amount_usd: float = 0.0
    lp_locked: bool = False
    lp_lock_duration_days: float = 0.0
    lp_provider_is_creator: bool = False

    # Social features
    has_twitter: bool = False
    has_telegram: bool = False
    twitter_account_age_days: float = 0.0
    telegram_member_count: int = 0

    # Contract features
    is_mintable: bool = False
    has_blacklist: bool = False
    buy_tax: float = 0.0
    sell_tax: float = 0.0

    # Label (set later)
    is_rug: Optional[bool] = None
    label_timestamp: Optional[str] = None

    def to_feature_vector(self) -> List[float]:
        """Convert to numeric feature vector for ML model."""
        return [
            self.age_minutes,
            self.time_to_first_lp_minutes,
            self.creator_wallet_age_days,
            self.creator_prev_tokens,
            self.creator_prev_rugs,
            self.creator_wallet_sol_balance,
            self.buyers_first_30s,
            self.buyers_first_5min,
            self.unique_buyers_1h,
            self.top_holder_pct,
            self.top5_holders_pct,
            self.buy_sell_ratio_5min,
            self.buy_sell_ratio_30min,
            self.volume_first_5min_usd,
            self.price_change_5min,
            self.price_change_30min,
            self.volatility_30min,
            self.lp_amount_usd,
            float(self.lp_locked),
            self.lp_lock_duration_days,
            float(self.lp_provider_is_creator),
            float(self.has_twitter),
            float(self.has_telegram),
            self.twitter_account_age_days,
            self.telegram_member_count,
            float(self.is_mintable),
            float(self.has_blacklist),
            self.buy_tax,
            self.sell_tax,
        ]

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class RugPrediction:
    token_address: str
    rug_probability: float      # 0.0-1.0
    confidence: float           # How confident the model is
    risk_level: str             # "SAFE", "CAUTION", "DANGER", "BLOCK"
    features_used: int          # How many features had data
    model_type: str             # "heuristic" or "ml"
    top_risk_factors: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.rug_probability < 0.60

    def summary(self) -> str:
        return (
            f"Rug Prob: {self.rug_probability*100:.1f}% | "
            f"Risk: {self.risk_level} | "
            f"Model: {self.model_type} | "
            f"Confidence: {self.confidence*100:.0f}%"
        )


class RugClassifier:
    """
    ML-powered rug pull predictor.
    Falls back to heuristic rules until enough training data exists.
    """

    def __init__(self,
                 block_threshold: float = 0.60,
                 caution_threshold: float = 0.40,
                 auto_label_threshold_pct: float = -80.0,
                 retrain_interval_days: int = 7):
        self.block_threshold = block_threshold
        self.caution_threshold = caution_threshold
        self.auto_label_threshold = auto_label_threshold_pct
        self.retrain_interval = retrain_interval_days

        self._model = None
        self._training_data: List[dict] = []
        self._pending_labels: Dict[str, dict] = {}  # tokens awaiting labels
        self._last_retrain: Optional[datetime] = None
        self._predictions_made = 0
        self._ml_predictions = 0
        self._blocks = 0

        os.makedirs("ml", exist_ok=True)
        self._load_training_data()
        self._load_model()

    async def predict(self, features: TokenFeatures) -> RugPrediction:
        """
        Predict rug probability for a token.
        Uses ML model if available and enough data exists,
        otherwise falls back to heuristic rules.
        """
        self._predictions_made += 1

        # Store features for later labeling
        self._pending_labels[features.token_address] = features.to_dict()

        if self._model and len(self._training_data) >= MIN_SAMPLES_FOR_ML:
            return await self._ml_predict(features)
        else:
            return self._heuristic_predict(features)

    def _ml_predict(self, features: TokenFeatures) -> RugPrediction:
        """Use trained ML model for prediction."""
        try:
            feature_vector = [features.to_feature_vector()]
            prob = self._model.predict_proba(feature_vector)[0][1]
            confidence = abs(prob - 0.5) * 2  # 0 at 50%, 1 at 0% or 100%

            self._ml_predictions += 1

            risk_level = self._prob_to_risk(prob)
            top_factors = self._extract_risk_factors(features, prob)

            prediction = RugPrediction(
                token_address=features.token_address,
                rug_probability=prob,
                confidence=confidence,
                risk_level=risk_level,
                features_used=len(features.to_feature_vector()),
                model_type="ml",
                top_risk_factors=top_factors
            )

            if prob >= self.block_threshold:
                self._blocks += 1
                logger.warning(
                    f"[RugML] 🛑 BLOCKED {features.token_address[:10]}... "
                    f"rug prob: {prob*100:.1f}%"
                )

            return prediction

        except Exception as e:
            logger.error(f"[RugML] ML prediction error: {e}")
            return self._heuristic_predict(features)

    def _heuristic_predict(self, features: TokenFeatures) -> RugPrediction:
        """Rule-based rug detection as fallback before ML model is ready."""
        risk_score = 0.0
        risk_factors = []
        data_count = 0

        # Creator wallet checks
        if features.creator_prev_rugs > 0:
            risk_score += 0.30 * features.creator_prev_rugs
            risk_factors.append(f"Creator has {features.creator_prev_rugs} prev rug(s)")
            data_count += 1

        if features.creator_wallet_age_days < 7:
            risk_score += 0.15
            risk_factors.append(f"Creator wallet only {features.creator_wallet_age_days:.0f} days old")
            data_count += 1

        # Buyer concentration (bot armies)
        if features.buyers_first_30s > 30:
            risk_score += 0.20
            risk_factors.append(f"{features.buyers_first_30s} buyers in first 30s (bot army)")
            data_count += 1

        # Holder concentration
        if features.top_holder_pct > 20:
            risk_score += 0.15
            risk_factors.append(f"Top holder owns {features.top_holder_pct:.1f}%")
            data_count += 1

        if features.top5_holders_pct > 60:
            risk_score += 0.10
            risk_factors.append(f"Top 5 hold {features.top5_holders_pct:.1f}%")
            data_count += 1

        # Contract features
        if features.is_mintable:
            risk_score += 0.25
            risk_factors.append("Mintable contract")
            data_count += 1

        if features.has_blacklist:
            risk_score += 0.20
            risk_factors.append("Has blacklist function")
            data_count += 1

        # LP not locked
        if not features.lp_locked:
            risk_score += 0.10
            risk_factors.append("LP not locked")
            data_count += 1

        if features.lp_provider_is_creator:
            risk_score += 0.10
            risk_factors.append("Creator provided LP (easy rug)")
            data_count += 1

        # No social links
        if not features.has_twitter and not features.has_telegram:
            risk_score += 0.10
            risk_factors.append("No social links")
            data_count += 1

        # Tax
        if features.sell_tax > 10:
            risk_score += 0.15
            risk_factors.append(f"High sell tax {features.sell_tax:.1f}%")
            data_count += 1

        # Normalize and cap
        prob = min(0.95, risk_score)
        confidence = min(0.8, data_count / 10)

        return RugPrediction(
            token_address=features.token_address,
            rug_probability=prob,
            confidence=confidence,
            risk_level=self._prob_to_risk(prob),
            features_used=data_count,
            model_type="heuristic",
            top_risk_factors=risk_factors[:5]
        )

    def label_token(self, token_address: str, is_rug: bool,
                     price_change_pct: float = 0.0):
        """
        Label a token as rug or legitimate after the fact.
        Called automatically when a token's price collapses.
        """
        pending = self._pending_labels.get(token_address)
        if not pending:
            return

        pending["is_rug"] = is_rug
        pending["label_timestamp"] = datetime.now(timezone.utc).isoformat()
        pending["price_change_pct"] = price_change_pct

        self._training_data.append(pending)
        del self._pending_labels[token_address]
        self._save_training_data()

        rug_count = sum(1 for t in self._training_data if t.get("is_rug"))
        legit_count = len(self._training_data) - rug_count

        logger.info(
            f"[RugML] Labeled {token_address[:10]}... as "
            f"{'RUG' if is_rug else 'LEGIT'} | "
            f"Dataset: {len(self._training_data)} total "
            f"({rug_count} rugs, {legit_count} legit)"
        )

        # Retrain if we have enough data and it's been a week
        if (len(self._training_data) >= MIN_SAMPLES_FOR_ML and
                self._should_retrain()):
            asyncio.create_task(self._retrain_model())

    async def auto_label_from_price(self, token_address: str,
                                     current_price: float,
                                     entry_price: float):
        """
        Automatically label tokens based on price performance.
        Called periodically to retroactively label pending tokens.
        """
        if entry_price <= 0:
            return
        change_pct = ((current_price - entry_price) / entry_price) * 100
        if change_pct <= self.auto_label_threshold:
            self.label_token(token_address, is_rug=True,
                              price_change_pct=change_pct)

    async def _retrain_model(self):
        """Retrain the ML model on accumulated data."""
        try:
            from sklearn.ensemble import GradientBoostingClassifier
            from sklearn.model_selection import cross_val_score
            import numpy as np

            logger.info(
                f"[RugML] Retraining on {len(self._training_data)} examples..."
            )

            # Build feature matrix
            X, y = [], []
            for sample in self._training_data:
                if sample.get("is_rug") is None:
                    continue
                try:
                    features = TokenFeatures(**{
                        k: v for k, v in sample.items()
                        if k in TokenFeatures.__dataclass_fields__
                    })
                    X.append(features.to_feature_vector())
                    y.append(1 if sample["is_rug"] else 0)
                except Exception:
                    continue

            if len(X) < MIN_SAMPLES_FOR_ML:
                logger.warning(
                    f"[RugML] Only {len(X)} valid samples — "
                    f"need {MIN_SAMPLES_FOR_ML} to train"
                )
                return

            X = np.array(X)
            y = np.array(y)

            # Train model
            model = GradientBoostingClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                min_samples_leaf=5,
                random_state=42
            )
            model.fit(X, y)

            # Cross-validate
            cv_scores = cross_val_score(model, X, y, cv=5, scoring="roc_auc")
            auc = cv_scores.mean()

            logger.info(
                f"[RugML] Model trained | "
                f"AUC: {auc:.3f} | "
                f"Samples: {len(X)} | "
                f"Rug rate: {y.mean()*100:.1f}%"
            )

            self._model = model
            self._last_retrain = datetime.now(timezone.utc)
            self._save_model()

        except ImportError:
            logger.warning(
                "[RugML] scikit-learn not installed. "
                "Run: pip install scikit-learn numpy"
            )
        except Exception as e:
            logger.error(f"[RugML] Training error: {e}")

    def _extract_risk_factors(self, features: TokenFeatures,
                               prob: float) -> List[str]:
        """Extract top contributing risk factors from heuristics."""
        factors = []
        if features.creator_prev_rugs > 0:
            factors.append(f"Serial rugger ({features.creator_prev_rugs} prev)")
        if features.buyers_first_30s > 30:
            factors.append(f"Bot army ({features.buyers_first_30s} in 30s)")
        if features.top_holder_pct > 20:
            factors.append(f"Whale concentration ({features.top_holder_pct:.1f}%)")
        if not features.lp_locked:
            factors.append("Unlocked liquidity")
        if features.sell_tax > 10:
            factors.append(f"High sell tax ({features.sell_tax:.1f}%)")
        return factors[:3]

    def _prob_to_risk(self, prob: float) -> str:
        if prob >= self.block_threshold:
            return "BLOCK"
        elif prob >= self.caution_threshold:
            return "DANGER"
        elif prob >= 0.25:
            return "CAUTION"
        else:
            return "SAFE"

    def _should_retrain(self) -> bool:
        if not self._last_retrain:
            return True
        days_since = (datetime.now(timezone.utc) - self._last_retrain).days
        return days_since >= self.retrain_interval

    def _save_model(self):
        try:
            with open(MODEL_FILE, "wb") as f:
                pickle.dump(self._model, f)
            logger.info(f"[RugML] Model saved to {MODEL_FILE}")
        except Exception as e:
            logger.error(f"[RugML] Model save error: {e}")

    def _load_model(self):
        if os.path.exists(MODEL_FILE):
            try:
                with open(MODEL_FILE, "rb") as f:
                    self._model = pickle.load(f)
                logger.info(f"[RugML] Loaded existing model from {MODEL_FILE}")
            except Exception as e:
                logger.warning(f"[RugML] Could not load model: {e}")

    def _save_training_data(self):
        try:
            with open(TRAINING_DATA_FILE, "w") as f:
                json.dump(self._training_data, f, indent=2)
        except Exception as e:
            logger.error(f"[RugML] Training data save error: {e}")

    def _load_training_data(self):
        if os.path.exists(TRAINING_DATA_FILE):
            try:
                with open(TRAINING_DATA_FILE, "r") as f:
                    self._training_data = json.load(f)
                labeled = sum(
                    1 for t in self._training_data
                    if t.get("is_rug") is not None
                )
                logger.info(
                    f"[RugML] Loaded {len(self._training_data)} training samples "
                    f"({labeled} labeled)"
                )
            except Exception as e:
                logger.warning(f"[RugML] Could not load training data: {e}")

    def get_stats(self) -> dict:
        labeled = sum(
            1 for t in self._training_data if t.get("is_rug") is not None
        )
        rug_count = sum(
            1 for t in self._training_data if t.get("is_rug") is True
        )
        return {
            "total_predictions": self._predictions_made,
            "ml_predictions": self._ml_predictions,
            "heuristic_predictions": self._predictions_made - self._ml_predictions,
            "blocks": self._blocks,
            "training_samples": len(self._training_data),
            "labeled_samples": labeled,
            "rug_samples": rug_count,
            "samples_until_ml": max(0, MIN_SAMPLES_FOR_ML - labeled),
            "model_active": self._model is not None,
            "pending_labels": len(self._pending_labels),
            "last_retrain": (
                self._last_retrain.isoformat()
                if self._last_retrain else None
            )
        }


async def extract_features_from_dexscreener(
    token_address: str,
    chain_id: str,
    pair_data: Optional[dict] = None,
    security_result=None
) -> TokenFeatures:
    """
    Helper to build a TokenFeatures object from available data.
    Combines DexScreener pair data with security check results.
    """
    features = TokenFeatures(
        token_address=token_address,
        chain_id=chain_id,
        timestamp=datetime.now(timezone.utc).isoformat()
    )

    if pair_data:
        features.lp_amount_usd = pair_data.get(
            "liquidity", {}
        ).get("usd", 0)
        txns = pair_data.get("txns", {})
        h1 = txns.get("h1", {})
        features.buyers_first_5min = h1.get("buys", 0)
        total = h1.get("buys", 0) + h1.get("sells", 0)
        features.buy_sell_ratio_5min = (
            h1.get("buys", 0) / total if total > 0 else 0.5
        )
        features.price_change_5min = pair_data.get(
            "priceChange", {}
        ).get("h1", 0) or 0
        features.has_twitter = bool(
            any(s.get("type") == "twitter"
                for s in pair_data.get("info", {}).get("socials", []))
        )
        features.has_telegram = bool(
            any(s.get("type") == "telegram"
                for s in pair_data.get("info", {}).get("socials", []))
        )

    if security_result:
        features.is_mintable = security_result.can_mint
        features.has_blacklist = security_result.has_blacklist
        features.buy_tax = security_result.buy_tax
        features.sell_tax = security_result.sell_tax
        features.lp_locked = security_result.liquidity_locked
        features.top_holder_pct = security_result.top10_concentration / 10
        features.top5_holders_pct = security_result.top10_concentration / 2
        features.creator_prev_rugs = 1 if security_result.dev_holding_pct > 20 else 0

    return features
