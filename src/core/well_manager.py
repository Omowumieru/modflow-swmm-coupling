"""
Well package management for MODFLOW-SWMM coupling.

This module handles well package data loading, rate updates, and API interactions.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional
from pyswmm import Nodes


class WellPackageManager:
    """Manages well package operations for sanitary nodes."""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.well_package_data = None
        self.well_rates = {}
        self.total_well_infiltration_m3_per_day = 0.0
        # Track head differences for CSV export
        self.head_difference_history = []

    def load_well_package_data(self, well_csv_path: str) -> bool:
        """
        Load well package data from CSV file.
        
        Args:
            well_csv_path: Path to the sanitary nodes CSV file
            
        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            self.well_package_data = pd.read_csv(well_csv_path)
            self.logger.info(f"  Loaded well package data: {len(self.well_package_data)} wells")
            
            for _, row in self.well_package_data.iterrows():
                node_name = row['node_name']
                self.well_rates[node_name] = 0.0
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error loading well package data: {e}")
            self.well_package_data = None
            return False

    def apply_well_rates(self, ml, stress_period: int, well_rates: Optional[Dict[str, float]] = None, initial_rates: bool = False) -> None:
        """
        Apply well rates to MODFLOW using the API.

        Args:
            ml: MODFLOW model object
            stress_period: Current stress period number
            well_rates: Dictionary of well rates to apply (optional)
            initial_rates: Whether to set initial rates to 0
        """
        try:
            well_package = ml.wel
            if well_package is None:
                self.logger.warning("  Well package not found in model")
                return
        
            mf6_spd = []
            for _, well_row in self.well_package_data.iterrows():
                layer = int(well_row['layer'])
                row_idx = int(well_row['row'])
                col_idx = int(well_row['col'])
                node_name = well_row['node_name']
                
                if initial_rates:
                    well_rate = 0.0
                elif well_rates is not None and node_name in well_rates:
                    well_rate = well_rates[node_name]
                else:
                    well_rate = self.well_rates.get(node_name, 0.0)
                
                mf6_spd.append(((layer, row_idx, col_idx), well_rate))
            
            dtype = [("nodelist", "O"), ("q", float)]
            mf6_spd_array = np.array(mf6_spd, dtype=dtype)
            
            well_package.stress_period_data.values = mf6_spd_array
            
            if initial_rates:
                self.logger.debug(f"  Set initial well rates to 0.0 using direct API")
            else:
                self.logger.debug(f"  Updated well package rates using direct API for stress period {stress_period}")
                        
        except Exception as e:
            self.logger.warning(f"  Direct well package update failed: {e}")

    def compute_sanitary_node_infiltration(self, swmm_nodes, nrows: int, ncols: int, head_array, 
                                         modflow_to_swmm_mapping: Dict, invert_elevations: Dict[str, float],
                                         conductance_positive: float, conductance_negative: float,
                                         swmm_node_inflow: Dict[str, float], model_name: str, data=None, day: int = 0) -> Dict[str, float]:
        """
        Compute groundwater infiltration into sanitary nodes identified by the SWMM classifier
        (data.swmm_classification['sanitary_nodes']) using head-based approach with different
        conductances for positive/negative head differences. Matches USGS approach where same
        Q is used for SWMM inflow and MODFLOW well extraction.

        Returns:
            Dictionary of well rates for MODFLOW
        """
        conductance_positive_mpd = conductance_positive
        conductance_negative_mpd = conductance_negative

        total_infiltration_nodes = 0
        total_infiltration_gpm = 0.0
        total_infiltration_m3_per_day = 0.0

        self.logger.debug(f"Computing groundwater infiltration for sanitary nodes")
        self.logger.debug(f"  MODFLOW to SWMM mapping: {len(modflow_to_swmm_mapping)} cells, invert elevations: {len(invert_elevations)} nodes")

        # Handle active-cell head array expansion
        total_cells = nrows * ncols
        if data is not None and hasattr(data, 'gwf') and data.gwf is not None:
            idomain = data.gwf.modelgrid.idomain.flatten()
            active_mask = (idomain > 0)
            n_active = int(np.sum(active_mask))
            
            if len(head_array) == n_active and n_active < total_cells:
                head_full = np.full(total_cells, -1e30)
                head_full[active_mask] = head_array
                self.logger.debug(f"  Expanded head array: {len(head_array)} active cells -> {total_cells} full grid")
            elif len(head_array) == total_cells:
                head_full = head_array
            else:
                self.logger.warning(
                    f"  head_array size ({len(head_array)}) doesn't match active cells ({n_active}) "
                    f"or full grid ({total_cells}). Using raw array."
                )
                head_full = head_array
        else:
            self.logger.warning("  No gwf model available for idomain - using raw head_array indexing")
            head_full = head_array

        well_rates_for_modflow = {}

        sanitary_nodes = set()
        if data is not None and getattr(data, 'swmm_classification', None):
            sanitary_nodes = data.swmm_classification.get('sanitary_nodes', set())

        for (row, col), node_list in modflow_to_swmm_mapping.items():
            if row >= nrows or col >= ncols:
                continue

            flat_index = row * ncols + col
            if flat_index >= len(head_full):
                continue

            gw_head = head_full[flat_index]

            # Skip inactive cells (HDRY / HNO)
            if gw_head < -1e10:
                continue

            for node_id in node_list:
                if node_id in sanitary_nodes:
                    invert_ft = invert_elevations.get(node_id, 0)
                    invert_m = invert_ft * 0.3048

                    head_diff = gw_head - invert_m
                    
                    if abs(head_diff) > 0.001:
                        # Track head difference for CSV export (only when GW head > invert)
                        if head_diff > 0:
                            self.head_difference_history.append({
                                'day': day,
                                'node_id': node_id,
                                'head_diff': head_diff
                            })
                        
                        if head_diff > 0:
                            conductance_mpd = conductance_positive_mpd
                            flow_direction = "into sanitary"
                        else:
                            conductance_mpd = conductance_negative_mpd
                            flow_direction = "from sanitary to GW"
                        
                        infiltration_volume_m3_per_day = abs(head_diff) * conductance_mpd
                        
                        if head_diff > 0:
                            inflow_gpm = infiltration_volume_m3_per_day * 264.172 / (24 * 60)
                        else:
                            inflow_gpm = -infiltration_volume_m3_per_day * 264.172 / (24 * 60)

                        total_infiltration_nodes += 1
                        total_infiltration_gpm += inflow_gpm
                        total_infiltration_m3_per_day += infiltration_volume_m3_per_day

                        self.logger.debug(f"  Node {node_id} (cell {row},{col}): head={gw_head:.3f}m, invert={invert_m:.3f}m, diff={head_diff:.3f}m, {flow_direction}, infiltration={infiltration_volume_m3_per_day:.6f} m³/day = {inflow_gpm:.4f} GPM")

                        try:
                            swmm_nodes[node_id].generated_inflow(inflow_gpm)
                            swmm_node_inflow[node_id] = swmm_node_inflow.get(node_id, 0.0) + inflow_gpm
                            
                            well_rate_m3_per_day = infiltration_volume_m3_per_day
                            if head_diff > 0:
                                well_rate_m3_per_day = -infiltration_volume_m3_per_day
                            
                            well_rates_for_modflow[node_id] = well_rate_m3_per_day
                        except KeyError:
                            self.logger.warning(f"Sanitary node '{node_id}' not found in SWMM model")
                    else:
                        self.logger.debug(f"  Node {node_id} (cell {row},{col}): head={gw_head:.3f}m, invert={invert_m:.3f}m, diff={head_diff:.3f}m - no significant head difference")

        self.logger.debug(f"Groundwater infiltration: {total_infiltration_nodes} nodes, {total_infiltration_gpm:.4f} GPM, {total_infiltration_m3_per_day:.6f} m³/day")
        
        # Update well package rates for next stress period
        if well_rates_for_modflow and self.well_package_data is not None:
            self.logger.debug(f"Updating well package rates for {len(well_rates_for_modflow)} wells")
            for node_name, well_rate in well_rates_for_modflow.items():
                self.well_rates[node_name] = well_rate
        
        self.total_well_infiltration_m3_per_day = total_infiltration_m3_per_day
        self.logger.debug(f"WellManager total_well_infiltration_m3_per_day: {self.total_well_infiltration_m3_per_day}")
        
        return well_rates_for_modflow

    def extract_invert_elevations(self, swmm_sim) -> Dict[str, float]:
        """Extract invert elevations for every SWMM node (junction, outfall, storage, divider)."""
        invert_elevations = {}
        try:
            for node in Nodes(swmm_sim):
                if node.is_junction() or node.is_outfall() or node.is_storage() or node.is_divider():
                    invert_elevations[node.nodeid] = node.invert_elevation
            self.logger.info(f"  Extracted invert elevations for {len(invert_elevations)} nodes")
        except Exception as e:
            self.logger.warning(f"  Could not extract invert elevations: {e}")
        return invert_elevations

    def export_head_differences_to_csv(self, output_path: str = "results/simulation/gw_head_differences.csv") -> None:
        """
        Export head differences (gw_elev - invert_elev) to CSV in pivot format.
        
        Args:
            output_path: Path to save the CSV file
        """
        import os
        
        if not self.head_difference_history:
            self.logger.warning("No head difference data to export")
            return
        
        try:
            df = pd.DataFrame(self.head_difference_history)
            
            pivot_df = df.pivot_table(
                index='day',
                columns='node_id',
                values='head_diff',
                aggfunc='first'
            )
            
            pivot_df = pivot_df.reindex(sorted(pivot_df.columns), axis=1)
            pivot_df = pivot_df.reset_index()
            
            for col in pivot_df.columns:
                if col != 'day':
                    pivot_df[col] = pivot_df[col].apply(lambda x: f"{x:.4f}" if pd.notna(x) else "")
            
            os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
            
            pivot_df.to_csv(output_path, index=False)
            self.logger.info(f"Exported {len(self.head_difference_history)} head difference records to {output_path}")
            
        except Exception as e:
            self.logger.error(f"Error exporting head differences: {e}")