"""
Post-simulation output generation for MODFLOW-SWMM coupling.

Runs after the coupled simulation completes. Produces:
  - groundwater-invert head differences CSV
  - subcatchment groundwater-state time series CSVs
  - mean groundwater depth map (PNG)
  - mean water table raster (GeoTIFF)

All outputs are written with the scenario tag appended to the filename so
different scenarios don't overwrite each other's outputs.
"""

from pathlib import Path

from plots.groundwater_depth import plot_mean_groundwater_depth, export_mean_head_to_tif


def generate_outputs(coupled_sim, output_dir="results/simulation"):
    """Generate all post-simulation outputs from a completed CoupledSimulation."""
    data = coupled_sim.data
    logger = coupled_sim.logger
    scenario = coupled_sim.scenario

    def with_suffix(filename):
        p = Path(output_dir) / filename
        return str(p.with_name(f"{p.stem}_{scenario}{p.suffix}"))

    logger.info("Exporting groundwater-invert head differences...")
    coupled_sim.well_manager.export_head_differences_to_csv(
        output_path=with_suffix("gw_head_differences.csv")
    )

    logger.info("Exporting subcatchment gw_state time series...")
    coupled_sim.swmm_run_manager.save_subcatchment_gw_state_csvs(scenario=scenario)

    logger.info("Generating mean groundwater depth map...")
    plot_mean_groundwater_depth(
        head_sum=data.head_sum,
        head_count=data.head_count,
        grid_top=data.grid_top,
        output_path=with_suffix("mean_groundwater_depth_map.png"),
        gwf=getattr(data, 'gwf', None),
        api_ml=data.api_ml,
        model_name=coupled_sim.model_name,
        grid_params=getattr(data, 'grid_params', None),
        logger=logger,
    )

    logger.info("Exporting mean water table to .tif file...")
    export_mean_head_to_tif(
        head_sum=data.head_sum,
        head_count=data.head_count,
        gwf=getattr(data, 'gwf', None),
        output_path=with_suffix("mean_water_table.tif"),
        logger=logger,
    )
