import numpy as np
import logging
from pyswmm import Nodes, Subcatchments


class StressPeriodManager:
    """Manages stress period transitions for MODFLOW-SWMM coupling."""

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)
    
    def _get_grid_dimensions(self, data, model_name):
        """
        Get grid dimensions from the MODFLOW API model.
        
        Falls back to proportioning matrix max values + 1 if API model is unavailable.
        
        Args:
            data: Data container with api_ml and proportioning_matrix
            model_name: Name of the MODFLOW model
            
        Returns:
            Tuple of (nrows, ncols)
        """
        try:
            ml = [m for m in data.api_ml.models if m.name.lower() == model_name.lower()][0]
            nrows, ncols = ml.shape[1], ml.shape[2]
            return nrows, ncols
        except Exception:
            # Fallback: derive from proportioning matrix
            if data.proportioning_matrix is not None:
                nrows = int(data.proportioning_matrix['row'].max()) + 1
                ncols = int(data.proportioning_matrix['col'].max()) + 1
                self.logger.warning(f"Could not get grid dims from API, using proportioning matrix: {nrows}x{ncols}")
                return nrows, ncols
            else:
                self.logger.error("Cannot determine grid dimensions - no API model or proportioning matrix available")
                raise RuntimeError("Cannot determine grid dimensions")

    def _expand_head_array(self, head_array, nrows, ncols, model_name, data):
        """
        Expand a potentially compressed (active-cells-only) head array to full grid size.

        The MF6 API returns "X" as a compressed array containing only active-cell values
        when idomain filters inactive cells. Indexing this with a full-grid flat index
        (row * ncols + col) reads the wrong cell and produces nonsense heads (e.g. 16 ft
        for a low-lying coastal area).

        Detects compression, expands using idomain mask, fills inactive cells with -1e30.
        Falls back to raw array with a warning if idomain is unavailable.

        Returns:
            head_full: Array of length nrows*ncols, safe to index with row*ncols+col
        """
        total_cells = nrows * ncols

        if len(head_array) == total_cells:
            return head_array  # already full grid

        try:
            ml = [m for m in data.api_ml.models if m.name.lower() == model_name.lower()][0]
            idomain = data.api_ml.mf6.get_value(
                data.api_ml.mf6.get_var_address("IDOMAIN", model_name, "DIS")
)
            active_mask = (idomain.flatten() > 0)
            n_active = int(np.sum(active_mask))

            if len(head_array) == n_active:
                head_full = np.full(total_cells, -1e30)
                head_full[active_mask] = head_array
                self.logger.debug(
                    f"  _expand_head_array: expanded {n_active} active -> {total_cells} full grid"
                )
                return head_full
            else:
                self.logger.warning(
                    f"  _expand_head_array: length {len(head_array)} matches neither "
                    f"active ({n_active}) nor full grid ({total_cells}). Using raw array."
                )
                return head_array

        except Exception as e:
            self.logger.warning(
                f"  _expand_head_array: could not expand via idomain ({e}). "
                f"Using raw array — heads may be wrong for compressed grids."
            )
            return head_array

    def handle_stress_period_start(self, sim, data, managers, model_name, modflow_delr, modflow_delc):
        """Run all start-of-stress-period bookkeeping in order."""
        nrows, ncols = self._get_grid_dimensions(data, model_name)
        recharge_manager = managers['recharge_manager']

        recharge_manager.load_original_recharge(data, managers['modflow_api_helper'], model_name)
        self._load_invert_elevations(data, managers['well_manager'])
        recharge_manager.initialize_recharge_array(sim, data, nrows, ncols)
        recharge_manager.apply_recharge_for_period(sim, data, model_name, modflow_delr, modflow_delc)

    def _load_invert_elevations(self, data, well_manager):
        """Load sanitary-node invert elevations from SWMM on first need."""
        if not data.invert_elevations:
            data.invert_elevations = well_manager.extract_invert_elevations(data.swmm_sim)

    def handle_stress_period_end(self, sim, data, managers, model_name, modflow_delr, modflow_delc):
        """Handle stress period end processing."""
        try:
            self.logger.info(f"  Processing stress period {sim.kper} end...")
            
            nrows, ncols = self._get_grid_dimensions(data, model_name)

            data.swmm_node_inflow = {}

            self._extract_modflow_drainage(data, model_name, nrows, ncols, managers)
            self._inject_drainage_to_swmm_nodes(data, managers['drainage_manager'], nrows, ncols)
            self._handle_sanitary_node_infiltration(sim, data, managers, model_name, nrows, ncols)

            if 'groundwater_elevation_manager' in managers:
                head_array_raw = data.api_ml.mf6.get_value(data.api_ml.mf6.get_var_address("X", model_name))
                head_array_full = self._expand_head_array(head_array_raw, nrows, ncols, model_name, data)
                managers['groundwater_elevation_manager'].update_swmm_groundwater_elevations(
                    data.swmm_sim, head_array_full, data.proportioning_matrix, nrows, ncols
                )
            swmm_results = self._run_swmm_24_hours(sim, data, managers['swmm_run_manager'])

            self._extract_infiltration_from_swmm(data, managers['surface_infiltration'], nrows, ncols, swmm_results, modflow_delr, modflow_delc)
            
            if hasattr(data, 'swmm_recharge_per_cell') and data.swmm_recharge_per_cell is not None:
                data.swmm_recharge_yesterday = data.swmm_recharge_per_cell.copy()

            self._accumulate_heads(data, model_name, nrows, ncols, sim.kper)

            # Advance lagged values for next day
            data.advance_lagged_fluxes_for_next_day()

            return {'swmm_results': swmm_results}
        except Exception as e:
            self.logger.error(f"  CRITICAL ERROR in stress period {sim.kper} end processing: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {'swmm_results': {}}
    
    def _extract_modflow_drainage(self, data, model_name, nrows, ncols, managers):
        """Extract MODFLOW drainage."""
        drainage_manager = managers.get('drainage_manager')
        if drainage_manager:
            data.modflow_drainage_volumes = drainage_manager.extract_drainage_from_modflow(data.api_ml, model_name, nrows, ncols)
    
    def _run_swmm_24_hours(self, sim, data, swmm_run_manager):
        """Run SWMM for 24 hours."""
        current_stress_period_day = sim.kper + 1
        start_hour = (current_stress_period_day - 1) * 24 + 1
        end_hour = current_stress_period_day * 24
        
        swmm_run_manager.advance_swmm_to_stress_period(data.swmm_sim, start_hour)
        swmm_results = swmm_run_manager.run_swmm_24_hours(
            data.swmm_sim, current_stress_period_day, start_hour, end_hour, data
        )
        return swmm_results
    
    def _extract_infiltration_from_swmm(self, data, surface_infiltration, nrows, ncols, swmm_results, modflow_delr, modflow_delc):
        """Extract infiltration from SWMM."""
        try:
            swmm_subcatchments = Subcatchments(data.swmm_sim)
            data.swmm_recharge_per_cell, data.swmm_coverage_mask = surface_infiltration.extract_infiltration(
                swmm_subcatchments, swmm_results['daily_infiltration_by_subcatchment'], data.proportioning_matrix,
                modflow_delr, modflow_delc, nrows, ncols
            )
            data.swmm_subcatchment_areas = surface_infiltration.swmm_subcatchment_areas
        except Exception as e:
            self.logger.error(f"Error extracting infiltration: {e}")
    
    def _inject_drainage_to_swmm_nodes(self, data, drainage_manager, nrows, ncols):
        """Inject drainage to SWMM nodes."""
        try:
            swmm_nodes = Nodes(data.swmm_sim)
            swmm_subcatchments = list(Subcatchments(data.swmm_sim))
            drainage_manager.inject_drainage_to_swmm_nodes(
                swmm_nodes, swmm_subcatchments, nrows, ncols,
                data.modflow_drainage_volumes, data.modflow_to_swmm_mapping,
                data.proportioning_matrix, data.swmm_node_inflow
            )
        except Exception as e:
            self.logger.warning(f"Could not inject drainage: {e}")
    
    def _handle_sanitary_node_infiltration(self, sim, data, managers, model_name, nrows, ncols):
        """Handle sanitary node infiltration."""
        if data.well_package_data is None:
            return
        try:
            swmm_nodes = Nodes(data.swmm_sim)
            head_array_raw = data.api_ml.mf6.get_value(data.api_ml.mf6.get_var_address("X", model_name))
            head_array = self._expand_head_array(head_array_raw, nrows, ncols, model_name, data)
            well_rates = managers['well_manager'].compute_sanitary_node_infiltration(
                swmm_nodes, nrows, ncols, head_array, data.modflow_to_swmm_mapping,
                data.invert_elevations, data.conductance_positive, data.conductance_negative,
                data.swmm_node_inflow, model_name, data,
                day=data.current_day_number
            )
            if well_rates:
                self._apply_well_rates_to_modflow(sim, data, managers, model_name, well_rates)
        except Exception as e:
            self.logger.warning(f"Could not compute sanitary infiltration: {e}")
    
    def _apply_well_rates_to_modflow(self, sim, data, managers, model_name, well_rates):
        """Apply well rates to MODFLOW."""
        try:
            ml = [m for m in data.api_ml.models if m.name.lower() == model_name.lower()][0]
            managers['well_manager'].apply_well_rates(ml, sim.kper + 1, well_rates=well_rates)
        except Exception as e:
            self.logger.warning(f"Could not update well rates: {e}")

    def _accumulate_heads(self, data, model_name, nrows, ncols, kper):
        """Accumulate full-grid heads into data.head_sum for post-sim mean-head outputs."""
        try:
            head_array_raw = data.api_ml.mf6.get_value(data.api_ml.mf6.get_var_address("X", model_name))
            head_full = self._expand_head_array(head_array_raw, nrows, ncols, model_name, data)

            if data.head_sum is None:
                data.head_sum = head_full.copy()
            else:
                data.head_sum += head_full
            data.head_count += 1
        except Exception as e:
            self.logger.warning(f"  head accumulation failed for stress period {kper}: {e}")