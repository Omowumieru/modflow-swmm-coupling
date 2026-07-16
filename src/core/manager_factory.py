"""
Coupling component organizer for MODFLOW-SWMM simulation.

This module creates and organizes all the specialized components needed for
coupled MODFLOW-SWMM simulations. Each component handles a specific aspect
of the coupling process (e.g., infiltration, drainage, timing).

The organizer ensures all components are properly initialized with logging
and provides easy access to each component during simulation.
"""

import logging
from typing import Dict, Any
from .modflow_api_helper import ModflowApiHelper
from .swmm_run_manager import SWMMRunManager
from .validation_manager import ValidationManager
from .well_manager import WellPackageManager
from .surface_infiltration import SurfaceInfiltration
from .coupling_coordinator import CouplingCoordinator
from .drainage_manager import DrainageManager
from .stress_period_manager import StressPeriodManager
from .groundwater_elevation_manager import GroundwaterElevationManager
from .recharge_manager import RechargeManager


class CouplingComponentOrganizer:
    """
    Organizes all components needed for MODFLOW-SWMM coupling simulation.
    
    This class creates and manages all the specialized components that handle
    different aspects of the coupling process:
    
    - Temporal coordination between MODFLOW stress periods and SWMM time steps
    - Surface water infiltration from SWMM subcatchments to MODFLOW recharge
    - Groundwater drainage from MODFLOW to SWMM nodes
    - Groundwater leakage to sanitary sewer nodes (well package)
    - Data validation and quality control
    - SWMM simulation control and data collection
    
    Each component is initialized with proper logging and can be accessed
    throughout the simulation for its specific functionality.
    """
    
    def __init__(self, logger: logging.Logger):
        """
        Initialize the coupling component organizer.
        
        Args:
            logger: Logger instance for all components to use
        """
        self.logger = logger
        self._components = {}
    
    def create_all_components(self) -> Dict[str, Any]:
        """
        Create all coupling components and return them as a dictionary.
        
        This sets up all the specialized components needed for the coupled
        simulation, each handling a specific aspect of the MODFLOW-SWMM
        water exchange process.
        
        Returns:
            Dictionary mapping component names to component instances
        """
        self._components = {
            'modflow_api_helper': ModflowApiHelper(self.logger),
            'swmm_run_manager': SWMMRunManager(self.logger),
            'validation_manager': ValidationManager(self.logger),
            'well_manager': WellPackageManager(self.logger),
            'surface_infiltration': SurfaceInfiltration(self.logger),
            'coupling_coordinator': CouplingCoordinator(self.logger),
            'drainage_manager': DrainageManager(self.logger),
            'stress_period_manager': StressPeriodManager(self.logger),
            'groundwater_elevation_manager': GroundwaterElevationManager(self.logger),
            'recharge_manager': RechargeManager(self.logger)
        }
        
        return self._components