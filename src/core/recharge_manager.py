"""
Recharge management for MODFLOW-SWMM coupling.

This module handles the MODFLOW recharge package: reading the original recharge
pattern, initializing the SWMM-derived per-cell recharge array, and applying
the lagged SWMM infiltration values to MODFLOW's RCH package each stress period.
"""

import logging
import numpy as np


class RechargeManager:
    """Owns the MODFLOW recharge side of the MODFLOW-SWMM coupling."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def load_original_recharge(self, data, modflow_api_helper, model_name):
        """Lazily extract the unmodified MODFLOW recharge pattern on first need."""
        if data.original_recharge is not None:
            return
        ml = [m for m in data.api_ml.models if m.name.lower() == model_name.lower()][0]
        data.original_recharge = modflow_api_helper.read_original_recharge(ml, model_name)
        if data.original_recharge is None:
            self.logger.error("Critical: Could not extract original MODFLOW recharge. Model cannot proceed.")
            raise RuntimeError("Could not extract original MODFLOW recharge. Check your MODFLOW model and RCH package.")

    def initialize_recharge_array(self, sim, data, nrows, ncols):
        """On SP 0 only, initialize the per-cell SWMM recharge array + coverage mask to zeros.

        SWMM hasn't run yet at SP 0, so there's no infiltration data to extract.
        Real values get populated at the end of each stress period.
        """
        if sim.kper != 0 or data.swmm_recharge_per_cell is not None:
            return

        total_cells = nrows * ncols
        data.swmm_recharge_per_cell = np.zeros(total_cells)
        data.swmm_coverage_mask = np.zeros(total_cells, dtype=bool)

    def apply_recharge_for_period(self, sim, data, model_name, modflow_delr, modflow_delc):
        """Push today's recharge pattern into MODFLOW's RCH package.

        SP 0: use the unmodified original MODFLOW recharge (no SWMM data yet).
        SP N>0: replace coupled cells with yesterday's SWMM infiltration rate (one-day lag);
                non-coupled cells keep the original recharge.
        """
        if sim.kper == 0:
            data.rch_array = data.original_recharge.copy()
            return

        try:
            ml = [m for m in data.api_ml.models if m.name.lower() == model_name.lower()][0]
            rcha = ml.rch
            if rcha is None:
                return

            updated_recharge = data.original_recharge.copy()
            mask = data.swmm_coverage_mask

            # Pick the source of lagged rates (yesterday preferred; today's array as fallback)
            if data.swmm_recharge_yesterday is not None and mask is not None:
                source = data.swmm_recharge_yesterday
                tag = "lagged"
            elif data.swmm_recharge_per_cell is not None and mask is not None:
                source = data.swmm_recharge_per_cell
                tag = "current (fallback)"
            else:
                rcha.stress_period_data["recharge"] = updated_recharge
                data.rch_array = updated_recharge
                return

            # Vectorized write: only coupled cells get the SWMM-derived rate
            n = min(len(mask), len(source), len(updated_recharge))
            updated_recharge[:n] = np.where(mask[:n], source[:n], updated_recharge[:n])

            cell_area_m2 = modflow_delr * modflow_delc
            volume = float(np.sum(source[:n] * mask[:n]) * cell_area_m2)
            self.logger.info(f"  Using {tag} SWMM infiltration as recharge: {volume:.6f} m³/day")

            rcha.stress_period_data["recharge"] = updated_recharge
            data.rch_array = updated_recharge

        except Exception as e:
            self.logger.warning(f"  Could not apply infiltration to MODFLOW recharge: {e}")
