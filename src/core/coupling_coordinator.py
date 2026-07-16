"""
Coupling coordination for MODFLOW-SWMM coupling.

This module handles the main coupling setup, coordination, and execution.
"""

import logging
import os
from pyswmm import Simulation


class CouplingCoordinator:
    """Coordinates the main coupled simulation execution."""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def setup_swmm_simulation(self, swmm_input_path: str) -> Simulation:
        """
        Set up SWMM simulation.
        
        Args:
            swmm_input_path: Path to SWMM input file
            
        Returns:
            SWMM simulation object
        """
        self.logger.info("Setting up SWMM simulation...")
        swmm_sim = Simulation(swmm_input_path)
        swmm_sim.step_advance(3600)  # 1-hour timesteps
        swmm_sim.start()
        return swmm_sim

    def run_modflow_simulation(self, dll_path: str, sim_ws: str, modflow_api_callback) -> None:
        """
        Run MODFLOW simulation with coupling callback.
        
        Args:
            dll_path: Path to MODFLOW DLL/dylib
            sim_ws: MODFLOW simulation workspace
            modflow_api_callback: Callback function for coupling
        """
        self.logger.info("Starting coupled simulation...")
        
        try:
            from modflowapi import run_simulation
            self.logger.info("  Calling modflowapi.run_simulation...")
            run_simulation(dll_path, sim_ws, modflow_api_callback, verbose=False)
            self.logger.info("  run_simulation completed successfully")
        except Exception as e:
            self.logger.error(f"  CRITICAL ERROR in run_simulation: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            self.logger.error(f"  Simulation will continue despite the error")
            return

    def find_well_csv(self, default_path: str = "results/sanitary_nodes.csv") -> str:
        """Check if default well CSV file exists."""
        if os.path.exists(default_path):
            return default_path
        return None