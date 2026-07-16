"""
Validation management for MODFLOW-SWMM coupling.

This module handles validation methods for the coupled simulation.
"""

import logging


class ValidationManager:
    """Manages validation operations for the coupled simulation."""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
