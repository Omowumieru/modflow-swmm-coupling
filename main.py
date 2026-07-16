import os
import sys
import typer
import logging
from pathlib import Path
from typing import Optional
import platform

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.getcwd(), 'src')))

from src.coupled_simulation import CoupledSimulation

app = typer.Typer()

def setup_logging(log_level: str = "INFO"):
    """
    Set up logging configuration.
    
    Args:
        log_level (str): Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)
    
    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('logs/simulation.log')
        ]
    )
    
    # Set specific loggers to avoid duplicate messages
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)

def get_default_dll_path():
    system = platform.system().lower()
    if "windows" in system:
        return Path("exe/win/libmf6.dll") 
    elif "darwin" in system:
        return Path("exe/mac/libmf6.dylib")
    elif "linux" in system:
        return Path("exe/linux/libmf6.so")
    else:
        raise RuntimeError("Unsupported operating system.")

@app.command()
def validate_setup(
    modflow_workspace: Path = typer.Option(
        "./modflow_ws",
        help="Path to MODFLOW workspace directory"
    ),
    swmm_input: Path = typer.Option(
        "./swmm_inp/Bowers_beach.inp",
        help="Path to SWMM input file"
    ),
    subcatchments_shapefile: Path = typer.Option(
        "shp_files/delineation_shpfile.shp",
        help="Path to subcatchments shapefile"
    ),
    nodes_shapefile: Path = typer.Option(
        "shp_files/swmm_junc_out.shp",
        help="Path to nodes shapefile"
    ),
    output_report: Optional[Path] = typer.Option(
        None,
        help="Path to save validation report (optional)"
    )
):
    """
    Validate the MODFLOW-SWMM coupling setup without running the simulation.
    """
    print("Validating coupling setup...")
    
    try:
        # Initialize coupled simulation
        coupled_sim = CoupledSimulation(
            model_name="Bowers_beach",
            log_level=logging.INFO
        )
        
        # Set up simulation data (this includes validation)
        coupled_sim.setup_simulation_data(
            modflow_workspace=str(modflow_workspace),
            swmm_input_path=str(swmm_input),
            subcatchments_shapefile=str(subcatchments_shapefile),
            nodes_shapefile=str(nodes_shapefile),
            validate_first=True
        )
        
        print("Validation completed successfully!")
        
        # Save report if requested
        if output_report:
            # The validation report is already logged during setup
            print(f"Validation report logged to simulation.log")
            
    except Exception as e:
        print(f"Error during validation: {str(e)}")
        raise

@app.command()
def run_simulation(
    modflow_workspace: Path = typer.Option(
        "./modflow_ws",
        help="Path to MODFLOW workspace directory"
    ),
    swmm_input: Path = typer.Option(
        "./swmm_inp/Bowers_beach.inp",
        help="Path to SWMM input file"
    ),
    dll_path: Optional[Path] = typer.Option(
        None,
        help="Path to MODFLOW DLL/dylib file (default: auto-detect based on OS)"
    ),
    subcatchments_shapefile: Path = typer.Option(
        "shp_files/delineation_shpfile.shp",
        help="Path to subcatchments shapefile"
    ),
    nodes_shapefile: Path = typer.Option(
        "shp_files/swmm_junc_out.shp",
        help="Path to nodes shapefile"
    ),
    well_csv: Optional[Path] = typer.Option(
        "results/sanitary_nodes.csv",
        help="Path to sanitary nodes CSV file for well package"
    ),
    model_name: str = typer.Option(
        "Bowers_beach",
        help="Name of the model"
    ),
    validate_first: bool = typer.Option(
        True,
        help="Validate setup before running simulation"
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        help="Directory to save simulation results (optional)"
    ),
    log_level: str = typer.Option(
        "INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR)"
    ),
    scenario: str = typer.Option(
        "baseline",
        help="Scenario tag (e.g. 'baseline', 'slr') — appended as suffix to all output filenames"
    )
):
    """
    Run the coupled MODFLOW-SWMM simulation with the specified parameters.
    """
    # Set up logging
    setup_logging(log_level)
    logger = logging.getLogger(__name__)
    
    logger.info("Initializing simulation components...")
    
    try:
        # Set dll_path dynamically if not provided
        if dll_path is None:
            dll_path = get_default_dll_path()
        
        # Initialize coupled simulation
        coupled_sim = CoupledSimulation(
            model_name=model_name,
            log_level=getattr(logging, log_level.upper()),
            scenario=scenario,
        )
        logger.info(f"Scenario tag: '{scenario}' — outputs will be saved as *_{scenario}.<ext>")
        
        # Run the coupled simulation with consolidated setup
        results = coupled_sim.run_coupled_simulation(
            modflow_workspace=str(modflow_workspace),
            swmm_input_path=str(swmm_input),
            subcatchments_shapefile=str(subcatchments_shapefile),
            nodes_shapefile=str(nodes_shapefile),
            dll_path=str(dll_path),
            sim_ws=str(modflow_workspace),
            well_csv_path=str(well_csv) if well_csv else None,
            validate_first=validate_first
        )
        
        logger.info("Simulation completed successfully!")

        from src.postprocess import generate_outputs
        generate_outputs(coupled_sim)
        
        # Save results if output directory is specified
        if output_dir:
            output_dir.mkdir(exist_ok=True)
            results_file = output_dir / "simulation_results.json"
            # Save results summary
            logger.info(f"Results saved to {results_file}")
        
        return results
        
    except Exception as e:
        logger.error(f"Error during simulation: {str(e)}")
        raise

if __name__ == "__main__":
    app() 