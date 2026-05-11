"""Semantic analysis service for Google Reviews.

Pipeline:
  1. Language detection (langdetect) — skip transformer for non-English, still run VADER
  2. VADER pass — fast rule-based compound score [-1, +1]
  3. Transformer pass — distilbert-base-uncased-finetuned-sst-2-english (lazy singleton)
  4. Label fusion — combine VADER + transformer into a 4-class label
  5. TF-IDF keyword extraction — top keywords from the review corpus per location batch

Analysis is idempotent — rows with an existing ReviewAnalysis are re-analyzed in place.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.google_reviews.models.db import GoogleReview, ReviewAnalysis, SentimentLabel

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-vader-distilbert"

# ---------------------------------------------------------------------------
# Lazy singletons (loaded once on first use)
# ---------------------------------------------------------------------------

_vader_analyzer = None
_transformer_pipeline = None


def _get_vader():
    global _vader_analyzer
    if _vader_analyzer is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _vader_analyzer = SentimentIntensityAnalyzer()
            logger.info("VADER analyzer initialized.")
        except ImportError:
            logger.warning("vaderSentiment not installed — VADER analysis disabled.")
    return _vader_analyzer


def _get_transformer():
    global _transformer_pipeline
    if _transformer_pipeline is None:
        try:
            from transformers import pipeline
            _transformer_pipeline = pipeline(
                "sentiment-analysis",
                model="distilbert-base-uncased-finetuned-sst-2-english",
                truncation=True,
                max_length=512,
            )
            logger.info("Transformer pipeline initialized (distilbert-sst2).")
        except ImportError:
            logger.warning("transformers not installed — transformer analysis disabled.")
        except Exception as exc:
            logger.warning("Transformer pipeline failed to load: %s", exc)
    return _transformer_pipeline


# ---------------------------------------------------------------------------
# Analysis result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    sentiment: SentimentLabel
    compound_score: float
    transformer_label: Optional[str] = None
    transformer_score: Optional[float] = None
    keywords: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    model_version: str = MODEL_VERSION


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------

def _detect_language(text: str) -> str:
    """Return ISO 639-1 language code, defaulting to 'en' on failure."""
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return "en"


def _vader_score(text: str) -> float:
    """Return VADER compound score in [-1, +1]. 0.0 on failure."""
    analyzer = _get_vader()
    if analyzer is None or not text:
        return 0.0
    scores = analyzer.polarity_scores(text)
    return float(scores.get("compound", 0.0))


def _transformer_result(text: str) -> Tuple[Optional[str], Optional[float]]:
    """Return (label, confidence) from transformer. (None, None) on failure."""
    pipe = _get_transformer()
    if pipe is None or not text:
        return None, None
    try:
        result = pipe(text[:512])[0]  # truncate to avoid OOM
        return str(result["label"]), float(result["score"])
    except Exception as exc:
        logger.debug("Transformer inference failed: %s", exc)
        return None, None


def _fuse_labels(
    compound: float,
    transformer_label: Optional[str],
    transformer_score: Optional[float],
    rating: int,
) -> SentimentLabel:
    """Combine VADER score + transformer + star rating into a 4-class label.

    Rules:
    - If transformer is available and confident (>= 0.85), use it as primary
    - Otherwise use VADER thresholds (>= 0.05 positive, <= -0.05 negative)
    - High rating (4-5) with low-confidence negative → override to neutral/positive
    - Low rating (1-2) with low-confidence positive → override to negative
    - Mixed = transformer and VADER disagree with moderate scores
    """
    vader_label: SentimentLabel
    if compound >= 0.05:
        vader_label = SentimentLabel.positive
    elif compound <= -0.05:
        vader_label = SentimentLabel.negative
    else:
        vader_label = SentimentLabel.neutral

    if transformer_label and transformer_score and transformer_score >= 0.85:
        t_label = SentimentLabel.positive if transformer_label.upper() == "POSITIVE" else SentimentLabel.negative
        # Detect disagreement → mixed
        if t_label != vader_label and vader_label != SentimentLabel.neutral:
            return SentimentLabel.mixed
        return t_label

    # Star rating correction when VADER is ambiguous
    if vader_label == SentimentLabel.neutral:
        if rating >= 4:
            return SentimentLabel.positive
        if rating <= 2:
            return SentimentLabel.negative

    return vader_label


def analyze_single(text: Optional[str], rating: int = 3, language: Optional[str] = None) -> AnalysisResult:
    """Run full analysis on a single review text.

    Transformer is skipped for non-English text (still VADER + rating).
    """
    if not text or not text.strip():
        # No text — infer from star rating alone
        if rating >= 4:
            sentiment = SentimentLabel.positive
        elif rating <= 2:
            sentiment = SentimentLabel.negative
        else:
            sentiment = SentimentLabel.neutral
        return AnalysisResult(sentiment=sentiment, compound_score=0.0)

    lang = language or _detect_language(text)
    compound = _vader_score(text)

    t_label: Optional[str] = None
    t_score: Optional[float] = None
    if lang.startswith("en"):
        t_label, t_score = _transformer_result(text)

    sentiment = _fuse_labels(compound, t_label, t_score, rating)
    return AnalysisResult(
        sentiment=sentiment,
        compound_score=compound,
        transformer_label=t_label,
        transformer_score=t_score,
        model_version=MODEL_VERSION,
    )


# ---------------------------------------------------------------------------
# TF-IDF keyword extraction
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "is", "was", "are", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "very", "just", "this", "that",
    "it", "its", "they", "them", "their", "we", "our", "us", "i", "my",
    "me", "he", "she", "his", "her", "you", "your", "so", "if", "as",
    "from", "not", "no", "what", "which", "who", "when", "where", "how",
    "all", "any", "each", "few", "more", "most", "other", "some", "such",
}


def _tokenize(text: str) -> List[str]:
    text = text.lower()
    tokens = re.findall(r"[a-z]{3,}", text)
    return [t for t in tokens if t not in _STOP_WORDS]


def extract_keywords_tfidf(texts: List[str], top_n: int = 5) -> List[List[str]]:
    """Return top_n TF-IDF keywords for each text in the corpus."""
    if not texts:
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        vectorizer = TfidfVectorizer(
            tokenizer=_tokenize,
            token_pattern=None,
            max_features=500,
            sublinear_tf=True,
        )
        tfidf_matrix = vectorizer.fit_transform(texts)
        feature_names = vectorizer.get_feature_names_out()
        results = []
        for i in range(tfidf_matrix.shape[0]):
            row = tfidf_matrix.getrow(i).toarray().flatten()
            top_indices = row.argsort()[-top_n:][::-1]
            keywords = [feature_names[idx] for idx in top_indices if row[idx] > 0]
            results.append(keywords)
        return results
    except ImportError:
        logger.warning("scikit-learn not installed — keyword extraction disabled.")
        return [[] for _ in texts]
    except Exception as exc:
        logger.warning("TF-IDF extraction failed: %s", exc)
        return [[] for _ in texts]


# ---------------------------------------------------------------------------
# AnalysisService — used by SyncService
# ---------------------------------------------------------------------------

class AnalysisService:
    """Batch semantic analysis writer for review rows in the DB."""

    async def batch_analyze(self, review_db_ids: List[int], db: AsyncSession) -> int:
        """Analyze reviews by DB id, skipping those already analyzed.

        Returns count of rows written.
        """
        if not review_db_ids:
            return 0

        # Load reviews (with existing analysis eagerly joined)
        stmt = (
            select(GoogleReview)
            .where(GoogleReview.id.in_(review_db_ids))
        )
        result = await db.execute(stmt)
        reviews: List[GoogleReview] = list(result.scalars().all())

        # Separate reviews needing analysis
        to_analyze: List[GoogleReview] = []
        for rev in reviews:
            # Reload analysis relationship
            await db.refresh(rev, ["analysis"])
            if rev.analysis is None:
                to_analyze.append(rev)

        if not to_analyze:
            return 0

        # TF-IDF over this batch
        texts = [rev.text or "" for rev in to_analyze]
        keyword_lists = extract_keywords_tfidf(texts, top_n=5)

        written = 0
        for rev, keywords in zip(to_analyze, keyword_lists):
            result_obj = analyze_single(
                text=rev.text,
                rating=rev.rating or 3,
                language=rev.language,
            )
            # Detect language if not already stored
            if not rev.language and rev.text:
                rev.language = _detect_language(rev.text)

            analysis_row = ReviewAnalysis(
                review_id=rev.id,
                sentiment=result_obj.sentiment,
                compound_score=result_obj.compound_score,
                transformer_label=result_obj.transformer_label,
                transformer_score=result_obj.transformer_score,
                topics=keywords,
                keywords=keywords,
                model_version=result_obj.model_version,
            )
            db.add(analysis_row)
            written += 1

        return written
