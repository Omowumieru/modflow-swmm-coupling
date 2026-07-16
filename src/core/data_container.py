"""
Data container for MODFLOW-SWMM coupling.

This module provides a container for all coupling data structures.
"""

class CouplingDataContainer:
    """Container for all coupling data structures."""

    def __init__(self):
        # Core coupling data structures
        self.proportioning_matrix = None
        self.modflow_to_swmm_mapping = {}
        self.swmm_node_inflow = {}
        self.modflow_drainage_volumes = {}

        # Infiltration and coverage data
        self.swmm_recharge_per_cell = None
        self.swmm_coverage_mask = None
        self.original_recharge = None
        self.rch_array = None
        self.swmm_recharge_yesterday = None  # For one-day lag implementation

        # Head tracking (mean accumulation only)
        self.head_sum = None
        self.head_count = 0

        # Well package data
        self.well_package_data = None

        # Invert elevations for sanitary nodes
        self.invert_elevations = {}

        # MODFLOW API objects
        self.mf6_api = None
        self.api_ml = None
        self.swmm_sim = None

        # SWMM simulation tracking
        self.swmm_inp_path = None
        self.current_day_number = 0

        # Subcatchment data (area used by swmm_run_manager for unit conversions)
        self.swmm_subcatchment_areas = {}

        # Conductance parameters for head-based infiltration
        self.conductance_positive = 1.0e-6 * 86400  # 1e-6 m²/s to m²/day
        self.conductance_negative = 0.0

        # Lagged SWMM infiltration → MODFLOW recharge (one-day lag)
        self.previous_swmm_infiltration = 0.0  # Yesterday's infiltration (today's recharge)
        self.current_swmm_infiltration = 0.0   # Today's infiltration (tomorrow's recharge)

    def advance_lagged_fluxes_for_next_day(self):
        """Advance lagged fluxes for the next stress period."""
        self.previous_swmm_infiltration = self.current_swmm_infiltration
        self.current_swmm_infiltration = 0.0