import logging

from pyswmm import Subcatchments


class GroundwaterElevationManager:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)

    def update_swmm_groundwater_elevations(self, swmm_sim, head_array,
                                           proportioning_matrix, nrows, ncols):
        """
        Update SWMM subcatchment groundwater elevations from MODFLOW heads.
        Uses area-weighted averaging when a subcatchment spans multiple cells.

        Args:
            swmm_sim: Active SWMM simulation object
            head_array: Flat MODFLOW head array (meters)
            proportioning_matrix: DataFrame with subcatchment-cell proportions
            nrows, ncols: MODFLOW grid dimensions
        """
        subcatchments = Subcatchments(swmm_sim)

        # Build a lookup dictionary for subcatchments by ID for quick access
        sub_lookup = {}
        for sub in subcatchments:
            sub_lookup[sub.subcatchmentid] = sub

        # Group by subcatchment and compute weighted-average head
        for sub_id, group in proportioning_matrix.groupby('subcatchment_id'):
            weighted_head = 0.0
            total_weight = 0.0

            for _, row in group.iterrows():
                r, c = int(row['row']), int(row['col'])
                flat_idx = r * ncols + c
                proportion = float(row['proportion_in_swmm'])

                cell_head = head_array[flat_idx]
                # Skip dry/inactive cells
                if cell_head > 1e10 or cell_head < -1e10:
                    continue

                weighted_head += cell_head * proportion
                total_weight += proportion

            if total_weight > 0:
                avg_head_m = weighted_head / total_weight 
                # Convert meters to feet for SWMM (1 m = 3.28084 ft)
                avg_head_ft = avg_head_m / 0.3048

                if sub_id in sub_lookup:
                    sub = sub_lookup[sub_id]
                    current_state = sub.gw_state
                    if current_state is None:
                        self.logger.warning(
                            f"  Sub {sub_id}: gw_state is None - no aquifer/groundwater defined"
                        )
                        continue
                    sub.gw_state = {
                        'theta': current_state['theta'],
                        'gwt_elev': avg_head_ft,
                        'max_infil_volume': current_state['max_infil_volume']
                    }
                    
                    updated_state = sub.gw_state
                    self.logger.debug(
                        f"  Sub {sub_id}: gwt_elev updated to {avg_head_ft:.3f} ft "
                        f"(read back={updated_state['gwt_elev']:.3f} ft, "
                        f"weighted from {total_weight:.4f} of cell area)"
                    )
                else:
                    self.logger.warning(f"  Subcatchment {sub_id} not found in SWMM")
            else:
                self.logger.warning(
                    f"  Sub {sub_id}: all overlapping cells are dry, skipping gwt_elev update"
                )