"""
Drainage management for MODFLOW-SWMM coupling.

This module handles drainage extraction from MODFLOW and injection to SWMM.
"""

import numpy as np
import logging
from typing import Dict, Tuple
from pyswmm import Nodes


class DrainageManager:
    """Handles drainage extraction and injection."""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.modflow_drainage_volumes = {}
        self.modflow_drainage_m3_per_day = 0.0
        self.total_drainage_processed = 0.0

    def extract_drainage_from_modflow(self, api_ml, model_name: str, nrows: int, ncols: int) -> Dict[Tuple[int, int], float]:
        """
        Extract drainage flows from MODFLOW using the drain package.
        
        Args:
            api_ml: MODFLOW API model object
            model_name: Name of the MODFLOW model
            nrows: Number of rows in the grid
            ncols: Number of columns in the grid
            
        Returns:
            Dictionary mapping (row, col) to drainage volume (m³/day)
        """
        self.modflow_drainage_volumes = {}
        
        try:
            drain_flow_tag = api_ml.mf6.get_var_address("SIMVALS", model_name, "DRN_0")
            drain_flows = api_ml.mf6.get_value(drain_flow_tag)
            self.logger.debug(f"  Drain flows shape: {drain_flows.shape}")

            negative_flows = drain_flows < 0
            drainage_count = np.sum(negative_flows)
            self.logger.debug(f"  Found {drainage_count} negative drain flows (drainage)")
            
            if drainage_count > 0:
                drainage_range = (drain_flows[negative_flows].min(), drain_flows[negative_flows].max())
                self.logger.debug(f"  Drainage flow range: {drainage_range[0]:.9f} to {drainage_range[1]:.9f} m³/day")
                
                for i, flow in enumerate(drain_flows):
                    if flow < 0:
                        row = i // ncols
                        col = i % ncols
                        cell_key = (row, col)
                        if cell_key not in self.modflow_drainage_volumes:
                            self.modflow_drainage_volumes[cell_key] = 0.0
                        self.modflow_drainage_volumes[cell_key] += abs(flow)
                        
                total_drainage = sum(self.modflow_drainage_volumes.values())
                self.logger.info(f"  Extracted drainage from MODFLOW: {len(self.modflow_drainage_volumes)} cells, total: {total_drainage:.6f} m³/day")
        except Exception as e:
            self.logger.warning(f"  Could not extract drainage using API: {e}")
            self.modflow_drainage_volumes = {}
            
        return self.modflow_drainage_volumes

    def inject_drainage_to_swmm_nodes(self, swmm_nodes, swmm_subcatchments, nrows: int, ncols: int,
                                    modflow_drainage_volumes: Dict[Tuple[int, int], float],
                                    modflow_to_swmm_mapping: Dict[Tuple[int, int], list],
                                    proportioning_matrix, swmm_node_inflow: Dict[str, float]) -> None:
        """
        Inject MODFLOW drainage into SWMM nodes using two methods:
        1. Direct cell-to-node mapping (for nodes directly in drainage cells)
        2. Subcatchment-based distribution (for nodes connected to subcatchments with drainage)
        """
        subcatchment_node_inflow = {}
        cell_based_node_inflow = {}

        total_drainage_volume = sum(abs(v) for v in modflow_drainage_volumes.values())
        self.logger.debug(f"Total MODFLOW drainage volume: {total_drainage_volume:.6f} m³/day")

        # Method 1: Direct cell-to-node mapping
        direct_drainage_count = 0
        for cell_key, node_list in modflow_to_swmm_mapping.items():
            cell_drn = modflow_drainage_volumes.get(cell_key, 0.0)
            if cell_drn > 0 and node_list:
                direct_drainage_count += 1
                node_share = cell_drn / len(node_list)
                inflow_gpm = node_share * 264.172 / (24 * 60)
                for node_id in node_list:
                    cell_based_node_inflow[node_id] = cell_based_node_inflow.get(node_id, 0.0) + inflow_gpm
                
                self.logger.debug(f"  Cell {cell_key} -> Nodes {node_list}: {cell_drn:.6f} m³/day = {inflow_gpm:.4f} GPM per node")

        self.logger.debug(f"  Direct cell-to-node drainage: {direct_drainage_count} cells")

        # Method 2: Subcatchment-based distribution
        for sub in swmm_subcatchments:
            sub_id = str(sub.subcatchmentid)
            conn = getattr(sub, 'connection')
            outlet_node = None
            if isinstance(conn, str):
                outlet_node = conn
            elif isinstance(conn, tuple) and len(conn) > 1:
                conn_type, conn_id = conn
                if conn_type == 2:
                    outlet_node = conn_id
            if not outlet_node:
                self.logger.warning(f"[DrainageManager] Skipping subcatchment {sub_id} because outlet_node is missing or invalid.")
                continue
            if outlet_node not in swmm_nodes:
                self.logger.warning(f"[DrainageManager] Skipping subcatchment {sub_id} because outlet_node '{outlet_node}' not found in SWMM nodes.")
                continue

            if not hasattr(proportioning_matrix, 'iterrows'):
                self.logger.error(f"proportioning_matrix is not a DataFrame (type: {type(proportioning_matrix)})")
                continue
                
            matching_cells = proportioning_matrix[proportioning_matrix['subcatchment_id'] == sub_id]
            total_ratioed = 0.0
            
            for _, r in matching_cells.iterrows():
                cell_key = (int(r['row']), int(r['col']))
                prop = float(r['proportion_in_modflow'])
                cell_drn = modflow_drainage_volumes.get(cell_key, 0.0)
                cell_contribution = cell_drn * prop
                total_ratioed += cell_contribution
                
                if cell_drn > 0:
                    self.logger.debug(f"[DrainageManager] Subcatchment {sub_id} | Cell {cell_key} | Drainage: {cell_drn:.6f} m³/day | Proportion: {prop:.6f} | Contribution: {cell_contribution:.6f} m³/day | Outlet node: {outlet_node}")

            inflow_gpm = total_ratioed * 264.172 / (24 * 60)
            if inflow_gpm > 0:
                subcatchment_node_inflow[outlet_node] = subcatchment_node_inflow.get(outlet_node, 0.0) + inflow_gpm
                self.logger.debug(f"[DrainageManager] Subcatchment-based: Subcatchment {sub_id} -> Node {outlet_node}: {total_ratioed:.6f} m³/day = {inflow_gpm:.4f} GPM")

        # Combine both methods and apply to SWMM nodes
        all_node_ids = set(subcatchment_node_inflow) | set(cell_based_node_inflow)
        nodes_with_inflow = 0
        total_applied_gpm = 0.0

        for node_id in all_node_ids:
            total_gpm = subcatchment_node_inflow.get(node_id, 0.0) + cell_based_node_inflow.get(node_id, 0.0)
            if total_gpm > 0:
                nodes_with_inflow += 1
                total_applied_gpm += total_gpm

            try:
                swmm_nodes[node_id].generated_inflow(total_gpm)
                swmm_node_inflow[node_id] = swmm_node_inflow.get(node_id, 0.0) + total_gpm
            except KeyError:
                self.logger.warning(f"Node '{node_id}' found in mapping but not in SWMM. Skipping inflow.")

        self.logger.debug(f"Applied drainage to {nodes_with_inflow} nodes, total: {total_applied_gpm:.4f} GPM")
        self.total_drainage_processed += total_applied_gpm
        
        drainage_to_nodes_m3_per_day = total_applied_gpm * 5.451  # GPM to m³/day
        self.modflow_drainage_m3_per_day = drainage_to_nodes_m3_per_day
        
        self.logger.info(f"  Total drainage applied to SWMM nodes: {drainage_to_nodes_m3_per_day:.6f} m³/day")