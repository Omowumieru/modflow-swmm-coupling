"""
Surface infiltration management for MODFLOW-SWMM coupling.

This module handles surface infiltration extraction from SWMM and application to MODFLOW.
"""

import numpy as np
import logging
from typing import Dict, Tuple


class SurfaceInfiltration:
    """Handles surface infiltration processing and application to MODFLOW grid.
    
    This class processes infiltration data from SWMM and distributes it to MODFLOW grid cells.
    """
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.swmm_subcatchment_areas = {}

    def extract_infiltration(self, swmm_subcatchments,
                           daily_infiltration_by_subcatchment: Dict[int, float],
                           proportioning_matrix, modflow_delr: float,
                           modflow_delc: float, nrows: int, ncols: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Extract infiltration from SWMM subcatchments using accumulated daily infiltration values.
        This method works with volumes to ensure mass conservation.
        
        Args:
            swmm_subcatchments: SWMM subcatchments object
            daily_infiltration_by_subcatchment: Dictionary mapping subcatchment ID to daily infiltration (in)
            proportioning_matrix: DataFrame with subcatchment-MODFLOW cell proportions
            modflow_delr: MODFLOW grid spacing in row direction (m)
            modflow_delc: MODFLOW grid spacing in column direction (m)
            nrows: Number of rows in the grid
            ncols: Number of columns in the grid
            
        Returns:
            Tuple of (recharge_array, coverage_mask)
        """
        temp_volume = np.zeros(nrows * ncols)
        coverage_mask = np.zeros(nrows * ncols, dtype=bool)
        
        self.swmm_subcatchment_areas = {}

        swmm_infiltration_volume = {}
        total_infiltration_volume = 0.0
        
        for subcatchment in list(swmm_subcatchments):
            try:
                sub_id = str(subcatchment.subcatchmentid)
                area_m2 = subcatchment.area * 4046.86
                self.swmm_subcatchment_areas[sub_id] = area_m2

                percent_impervious = subcatchment.percent_impervious
                percent_pervious = 100.0 - percent_impervious
                pervious_area_m2 = area_m2 * (percent_pervious / 100.0)

                daily_infiltration_total_in = daily_infiltration_by_subcatchment.get(sub_id, 0.0)
                infiltration_m_per_day = daily_infiltration_total_in * 0.0254

                infiltration_volume_m3_per_day = infiltration_m_per_day * pervious_area_m2
                
                self.logger.debug(
                    f"  Sub {sub_id}: {daily_infiltration_total_in:.6f} in -> {infiltration_m_per_day:.6f} m/day, "
                    f"area={area_m2:.1f} m², {percent_impervious:.1f}% impervious, "
                    f"pervious={pervious_area_m2:.1f} m², volume={infiltration_volume_m3_per_day:.6f} m³/day"
                )
                
                swmm_infiltration_volume[sub_id] = infiltration_volume_m3_per_day
                total_infiltration_volume += infiltration_volume_m3_per_day
                
            except Exception as e:
                self.logger.error(f"Error processing subcatchment volume for {sub_id}: {e}")
                continue
        
        for _, row in proportioning_matrix.iterrows():
            try:
                row_idx, col_idx = int(row['row']), int(row['col'])
                subcatchment_id = str(row['subcatchment_id'])
                proportion_in_swmm = float(row['proportion_in_swmm'])
                
                if subcatchment_id in swmm_infiltration_volume:
                    sub_volume = swmm_infiltration_volume[subcatchment_id]
                    cell_contribution_volume = sub_volume * proportion_in_swmm
                    flat_index = (row_idx * ncols) + col_idx
                    
                    temp_volume[flat_index] += cell_contribution_volume
                    coverage_mask[flat_index] = True
                    
            except Exception as e:
                self.logger.error(f"Error distributing infiltration volume for subcatchment {subcatchment_id}: {e}")
                continue
        
        temp_recharge = np.zeros(nrows * ncols)
        cell_area_m2 = modflow_delr * modflow_delc

        if cell_area_m2 > 0:
            non_zero_mask = temp_volume > 0
            temp_recharge[non_zero_mask] = temp_volume[non_zero_mask] / cell_area_m2
            
        self.logger.debug(f"Total SWMM infiltration volume distributed: {np.sum(temp_volume):.6f} m³/day")

        return temp_recharge, coverage_mask