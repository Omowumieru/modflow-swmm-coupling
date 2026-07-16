import flopy
import numpy as np
from typing import Tuple
import logging


class ModflowLoader:
    def __init__(self, sim_workspace: str, verbosity_level: int = 0):
        self.sim_workspace = sim_workspace
        self.verbosity_level = verbosity_level
        self.sim = None
        self.gwf = None
        self._load_simulation()

    def _load_simulation(self) -> None:
        """Load the MODFLOW 6 simulation and groundwater flow model."""
        try:
            self.sim = flopy.mf6.MFSimulation.load(
                sim_ws=self.sim_workspace,
                verbosity_level=self.verbosity_level
            )
            self.gwf = self.sim.get_model()
            logging.info(f"Successfully loaded MODFLOW simulation from {self.sim_workspace}")
        except Exception as e:
            logging.error(f"Error loading MODFLOW simulation: {str(e)}")
            raise

    def get_model_dimensions(self) -> Tuple[int, int, int]:
        """Get the model dimensions (nlay, nrow, ncol)."""
        if self.gwf is None:
            raise ValueError("Model not loaded. Call _load_simulation first.")
        return self.gwf.modelgrid.nlay, self.gwf.modelgrid.nrow, self.gwf.modelgrid.ncol

    def get_grid_parameters(self) -> Tuple[float, float, float, float, float, float]:
        """
        Extract MODFLOW grid parameters from the groundwater flow model.

        Returns xmin, xmax, ymin, ymax, delr, delc.
        """
        if self.gwf is None:
            raise ValueError("Model not loaded. Call _load_simulation first.")

        grid = self.gwf.modelgrid
        delr = grid.delr[0]
        delc = grid.delc[0]
        xmin = grid.xoffset
        xmax = grid.xoffset + np.sum(grid.delr)
        ymin = grid.yoffset
        ymax = grid.yoffset + np.sum(grid.delc)

        return xmin, xmax, ymin, ymax, delr, delc

    def get_groundwater_model(self) -> flopy.mf6.ModflowGwf:
        """Get the groundwater flow model."""
        if self.gwf is None:
            raise ValueError("Model not loaded. Call _load_simulation first.")
        return self.gwf