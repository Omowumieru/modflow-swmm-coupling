#This is the main file that runs the simulation and is called from main.py to run the simulation from the command line

import numpy as np
from modflowapi import Callbacks
import logging
import traceback
import geopandas as gpd
from .core.manager_factory import CouplingComponentOrganizer
from .core.data_container import CouplingDataContainer
from .core.coupling_coordinator import CouplingCoordinator
from .utils import (
    ModflowLoader,
    ModflowUtil,
    map_nodes_to_modflow_grid_by_coordinates,
    calculate_swmm_modflow_proportions,
    classify_swmm_objects,
)
from .validation import CouplingValidator


class CoupledSimulation:
    def __init__(self, model_name, delr=None, delc=None, log_level=logging.INFO,
                 conductance_positive=None, conductance_negative=None, stress_period_limit=None,
                 scenario="baseline"):
        """
        Initialize coupled MODFLOW-SWMM simulation.

        Args:
            model_name: Name of the MODFLOW model
            delr: MODFLOW grid spacing in row direction
            delc: MODFLOW grid spacing in column direction
            log_level: Logging level
            conductance_positive: Conductance for positive head differences
            conductance_negative: Conductance for negative head differences
            stress_period_limit: Maximum stress period to run (for testing specific periods)
            scenario: Scenario tag (e.g. "baseline", "slr") — appended as suffix to all output files
        """

        self.model_name = model_name
        self.scenario = scenario

        self.modflow_delr = delr
        self.modflow_delc = delc
        self.conductance_positive = conductance_positive
        self.conductance_negative = conductance_negative
        self.stress_period_limit = stress_period_limit
        
        
        
        # Set up logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)
        
        # Initialize data container
        self.data = CouplingDataContainer()

        # Initialize managers
        self._initialize_managers(log_level)
        
        # Initialize coupling coordinator
        self.coupling_coordinator = CouplingCoordinator(self.logger)
        
        # Track total stress periods for progress
        self.total_stress_periods = None

        

    def setup_simulation_data(self, modflow_workspace, swmm_input_path, subcatchments_shapefile,
                            nodes_shapefile, well_csv_path=None, validate_first=True):
        self.logger.info("Setting up simulation data...")
        try:
            self.data.swmm_classification = classify_swmm_objects(swmm_input_path)
            cls = self.data.swmm_classification
            self.logger.info(
                f"SWMM classification: {len(cls['sanitary_nodes'])} sanitary nodes "
                f"(seeds: {sorted(cls['dwf_seeds'])}), "
                f"{len(cls['sanitary_conduits'])} sanitary conduits, "
                f"{len(cls['sanitary_outfalls'])} sanitary outfalls"
            )
            if not cls['sanitary_nodes']:
                self.logger.warning(
                    "SWMM classification found no sanitary nodes — check for an [DWF] section "
                    "in the .inp file. Sanitary-sewer coupling will be a no-op."
                )
            nrow, ncol, proportioning_matrix, modflow_to_swmm_mapping = self._load_grid_and_proportions(
                modflow_workspace, subcatchments_shapefile, nodes_shapefile
            )
            if validate_first:
                self._validate_coupling(proportioning_matrix, modflow_to_swmm_mapping, nrow, ncol, swmm_input_path)
            self._store_spatial_data(proportioning_matrix, modflow_to_swmm_mapping, swmm_input_path)
            self._setup_well_and_swmm(well_csv_path, swmm_input_path)
            self.logger.info("Simulation data setup completed successfully!")
        except Exception as e:
            self.logger.error(f"Error during simulation data setup: {str(e)}")
            raise

    def _load_grid_and_proportions(self, modflow_workspace, subcatchments_shapefile, nodes_shapefile):
        loader = ModflowLoader(sim_workspace=str(modflow_workspace))
        gwf = loader.get_groundwater_model()
        self.data.gwf = gwf
        xmin, xmax, ymin, ymax, delr, delc = loader.get_grid_parameters()
        nlay, nrow, ncol = loader.get_model_dimensions()
        self.modflow_delr = delr
        self.modflow_delc = delc
        self.data.grid_top = gwf.modelgrid.top.flatten() if hasattr(gwf.modelgrid, 'top') else None
        gdf_grid = ModflowUtil().get_modflow_grid_as_gdf(gwf)
        gdf_subcatchment = gpd.read_file(subcatchments_shapefile)
        proportioning_matrix = calculate_swmm_modflow_proportions(gdf_grid, gdf_subcatchment, layer=0)
        _, modflow_to_swmm_mapping = map_nodes_to_modflow_grid_by_coordinates(
            gdf_grid, str(nodes_shapefile), xmin, xmax, ymin, ymax, delr, delc,
            sanitary_nodes=self.data.swmm_classification['sanitary_nodes'],
        )
        return nrow, ncol, proportioning_matrix, modflow_to_swmm_mapping

    def _validate_coupling(self, proportioning_matrix, modflow_to_swmm_mapping, nrow, ncol, swmm_input_path):
        self.logger.info("Validating setup before simulation...")
        validator = CouplingValidator()
        report = validator.generate_coupling_report(
            proportioning_matrix=proportioning_matrix,
            modflow_to_swmm_mapping=modflow_to_swmm_mapping,
            grid_shape=(nrow, ncol),
            swmm_input_path=str(swmm_input_path),
            swmm_classification=self.data.swmm_classification,
        )
        self.logger.info(report)

    def _store_spatial_data(self, proportioning_matrix, modflow_to_swmm_mapping, swmm_input_path):
        self.data.proportioning_matrix = proportioning_matrix
        self.data.modflow_to_swmm_mapping = modflow_to_swmm_mapping
        self.data.swmm_inp_path = swmm_input_path

    def _setup_well_and_swmm(self, well_csv_path, swmm_input_path):
        if well_csv_path:
            self.load_well_package_data(well_csv_path)
        else:
            default_well_csv = self.coupling_coordinator.find_well_csv()
            if default_well_csv:
                self.load_well_package_data(default_well_csv)
            else:
                self.logger.info("No well package data provided - will use legacy groundwater leakage calculation")
        self.data.swmm_sim = self.coupling_coordinator.setup_swmm_simulation(swmm_input_path)

    def _initialize_managers(self, log_level):
        """Initialize all coupling managers."""
        # Initialize component organizer
        self.component_organizer = CouplingComponentOrganizer(self.logger)
        
        # Create all coupling components
        self.managers = self.component_organizer.create_all_components()
        
        # Get individual components for easy access
        self.modflow_api_helper = self.managers['modflow_api_helper']
        self.swmm_run_manager = self.managers['swmm_run_manager']
        self.well_manager = self.managers['well_manager']
        self.surface_infiltration = self.managers['surface_infiltration']
        self.drainage_manager = self.managers['drainage_manager']
        self.stress_period_manager = self.managers['stress_period_manager']
        self.validation_manager = self.managers['validation_manager']
        self.recharge_manager = self.managers['recharge_manager']
        
        # Set conductance parameters
        if self.conductance_positive is not None:
            self.data.conductance_positive = self.conductance_positive
        if self.conductance_negative is not None:
            self.data.conductance_negative = self.conductance_negative

    def load_well_package_data(self, well_csv_path):
        """Load well package data using well manager."""
        if self.well_manager.load_well_package_data(well_csv_path):
            self.data.well_package_data = self.well_manager.well_package_data

    def modflow_api_callback(self, sim, callback_step):
        """Main coupling callback using stress period manager with comprehensive error handling."""
        try:
            # Check stress period limit for testing
            if self.stress_period_limit is not None and sim.kper > self.stress_period_limit:
                self.logger.info(f"  Skipping stress period {sim.kper} (beyond limit {self.stress_period_limit})")
                return
            
            if callback_step == Callbacks.initialize:
                self.logger.info("=== MODFLOW-SWMM Coupling Initialization ===")
                init_data = self.modflow_api_helper.modflow_initialize_callback(
                    sim, self.model_name, self.modflow_delr, self.modflow_delc,
                    self.data.conductance_positive, self.data.conductance_negative,
                    self.validation_manager
                )
                self.data.api_ml = init_data['api_ml']
                self.data.original_recharge = init_data['original_recharge']       
                
                # Store original recharge for future use
                if self.data.original_recharge is not None:
                    # Check what was stored in original_recharge
                    if self.logger.isEnabledFor(logging.DEBUG):
                        self.logger.debug(f"  Stored original_recharge in self.data - sum = {np.sum(self.data.original_recharge):.9f} m³/day")
                        self.logger.debug(f"  Stored original_recharge in self.data - range = {np.min(self.data.original_recharge):.9f} to {np.max(self.data.original_recharge):.9f} m/day")
                        self.logger.debug(f"  Stored original_recharge in self.data - non-zero cells = {np.count_nonzero(self.data.original_recharge)}")
                else:
                    self.logger.error(f"  Original_recharge is None after initialization!")
                
                # Store total stress periods for progress tracking
                self.total_stress_periods = init_data['nper']
                
                self.nrows, self.ncols = init_data['nrows'], init_data['ncols']

                # Log stress period limit if set
                if self.stress_period_limit is not None:
                    self.logger.info(f"  TESTING MODE: Will run only stress periods 0-{self.stress_period_limit}")
                
                self.logger.info("=== Coupling Initialization Complete ===")
                return
            
            if self.modflow_api_helper.should_skip_callback(sim, callback_step):
                return

            # Check if SWMM simulation is complete and stop further processing if so
            if self.swmm_run_manager.is_swmm_simulation_complete(self.data.swmm_sim):
                if callback_step in (Callbacks.stress_period_start, Callbacks.stress_period_end):
                    self.logger.info(f"SWMM simulation is complete. Skipping callback for stress period {sim.kper}.")
                    return
            
            if callback_step == Callbacks.stress_period_start:
                # Only log stress period start for debugging if needed
                if self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug(f"=== Stress Period {sim.kper} Start ===")
                self._handle_stress_period_start(sim)
            elif callback_step == Callbacks.stress_period_end:
                # Only log stress period end for debugging if needed
                if self.logger.isEnabledFor(logging.DEBUG):
                    self.logger.debug(f"=== Stress Period {sim.kper} End ===")
                self._handle_stress_period_end(sim)
                
        except Exception as e:
            self.logger.error(f"  ERROR in coupling callback for stress period {sim.kper}, step {callback_step}: {e}")
            self.logger.error(f"  Error type: {type(e).__name__}")
            import traceback
            self.logger.error(f"  Full traceback:")
            self.logger.error(traceback.format_exc())
            
            # Don't raise the exception - this prevents the restart
            # Instead, log the error and continue
            self.logger.error(f"  Continuing simulation despite the error")
            return

    def _handle_stress_period_start(self, sim):
        """Delegate start-of-period work to the stress period manager."""
        self.stress_period_manager.handle_stress_period_start(
            sim, self.data, self.managers, self.model_name, self.modflow_delr, self.modflow_delc
        )

    def _handle_stress_period_end(self, sim):
        """Delegate end-of-period work to the stress period manager."""
        try:
            self.stress_period_manager.handle_stress_period_end(
                sim, self.data, self.managers, self.model_name, self.modflow_delr, self.modflow_delc
            )
            self.data.current_day_number = sim.kper + 1
        except Exception as e:
            self.logger.error(f"  ERROR in stress period {sim.kper} end handler: {e}")
            self.logger.error(f"  Error type: {type(e).__name__}")
            self.logger.error(traceback.format_exc())
            self.logger.error(f"  Continuing simulation despite the error")

    def run_coupled_simulation(self, modflow_workspace, swmm_input_path, subcatchments_shapefile, 
                             nodes_shapefile, dll_path, sim_ws, well_csv_path=None, validate_first=True):
        """
        Run the complete coupled MODFLOW-SWMM simulation.
        
        Args:
            modflow_workspace: Path to MODFLOW workspace directory
            swmm_input_path: Path to SWMM input file
            subcatchments_shapefile: Path to subcatchments shapefile
            nodes_shapefile: Path to nodes shapefile
            dll_path: Path to MODFLOW DLL/dylib
            sim_ws: MODFLOW simulation workspace
            well_csv_path: Path to sanitary nodes CSV file for well package
            validate_first: Whether to validate setup before running simulation
        """
        try:
            # Set up simulation data
            self.setup_simulation_data(
                modflow_workspace, swmm_input_path, subcatchments_shapefile,
                nodes_shapefile, well_csv_path, validate_first
            )
            
            # Run the coupled simulation
            self.coupling_coordinator.run_modflow_simulation(dll_path, sim_ws, self.modflow_api_callback)

            # Finalize SWMM simulation to ensure reports are written
            if self.data.swmm_sim:
                self.swmm_run_manager.finalize_swmm_simulation(self.data.swmm_sim)

            self.logger.info("=== Coupled Simulation Completed Successfully ===")


        except Exception as e:
            self.logger.error(f"Error during coupled simulation: {str(e)}")