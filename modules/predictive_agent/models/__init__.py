"""
ML Models Package

Machine learning models for predictive maintenance across different vehicle components.
"""

from .tire_model import TirePredictionModel
from .model_registry import ModelRegistry

__all__ = [
    'TirePredictionModel',
    'ModelRegistry'
]