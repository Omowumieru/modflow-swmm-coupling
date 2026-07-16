"""
MODFLOW API helper for the coupled simulation callback flow.

Provides utilities for handling the modflowapi callback lifecycle:
initialization, duplicate-event suppression, and reading the original
RCH package contents at simulation start.
"""

import logging
import numpy as np
from typing import Dict, Any


class ModflowApiHelper:
    """Helper utilities for the MODFLOW API callback lifecycle."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._last_logged_step = None

    def modflow_initialize_callback(self, sim, model_name: str, modflow_delr, modflow_delc,
                         conductance_positive: float, conductance_negative: float,
                         validation_manager) -> Dict[str, Any]:
        """
        Handle MODFLOW initialization callback.

        Returns:
            Dictionary with initialization data
        """
        self.logger.info("=== MODFLOW-SWMM Coupling Initialized ===")

        api_ml = sim
        self.logger.info(f"ApiSimulation model names found: {api_ml.model_names}")

        ml = [m for m in api_ml.models if m.name.lower() == model_name.lower()][0]
        nrows, ncols = ml.shape[1], ml.shape[2]
        nper = int(ml.nper)
        self.logger.info(f"  Model: {ml.name}, Grid: {nrows}x{ncols}, Stress Periods: {nper}")

        original_recharge = None
        self.logger.info(f"  Recharge will be read when needed (API may not be initialized yet)")

        if modflow_delr is not None and modflow_delc is not None:
            self.logger.info(f"  Cell sizes: delr={modflow_delr:.2f}, delc={modflow_delc:.2f}, area={modflow_delr * modflow_delc:.2f} m2")
        else:
            self.logger.warning(f"  Warning: Grid parameters not available.")

        self.logger.info(f"  Conductances: positive={conductance_positive:.2e}, negative={conductance_negative:.2e} m2/day")

        return {
            'api_ml': api_ml, 'ml': ml,
            'nrows': nrows, 'ncols': ncols, 'nper': nper,
            'original_recharge': original_recharge
        }

    def should_skip_callback(self, sim, callback_step) -> bool:
        """Check if callback should be skipped to prevent duplicate logging."""
        current_step = (sim.kper, sim.kstp, callback_step)
        if current_step == self._last_logged_step:
            return True
        self._last_logged_step = current_step
        return False

    def read_original_recharge(self, ml, model_name: str) -> np.ndarray:
        """Read original recharge from MODFLOW model."""
        try:
            rcha = ml.rch
            if rcha is not None and hasattr(rcha, 'stress_period_data'):
                original_recharge = rcha.stress_period_data["recharge"].copy()
                self.logger.info(f"  Successfully read recharge from RCH package")
                self.logger.info(f"  Original recharge sum = {np.sum(original_recharge):.9f} m3/day")
                self.logger.info(f"  Original recharge range = {np.min(original_recharge):.9f} to {np.max(original_recharge):.9f} m/day")
                return original_recharge
            else:
                self.logger.error(f"  Could not access RCH package or stress_period_data")
                return None
        except Exception as e:
            self.logger.error(f"  Error reading original recharge: {e}")
            return None
