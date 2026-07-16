import flopy
import geopandas as gpd
from shapely.geometry import Polygon


class ModflowUtil:
    def get_modflow_grid_as_gdf(self, gwf: flopy.mf6.ModflowGwf) -> gpd.GeoDataFrame:
        """
        Convert the MODFLOW 6 model grid to a GeoDataFrame.

        Each grid cell becomes a polygon with its layer, row, and column stored
        as attributes — used for spatial overlay with SWMM subcatchments.
        """
        modflow_grid = gwf.modelgrid
        grid_type = modflow_grid.grid_type.lower()

        if grid_type not in ["structured", "vertex"]:
            raise ValueError(f"Unsupported grid type: {grid_type}. Supports structured and vertex grids.")

        nlay = modflow_grid.nlay
        grid_crs = modflow_grid.crs
        modflow_polygons = []
        layers, rows, cols = [], [], []

        for cell in range(modflow_grid.ncpl * nlay):
            vertices = modflow_grid.get_cell_vertices(cell)
            if vertices:
                modflow_polygons.append(Polygon(vertices))

                if grid_type == "structured":
                    lrc = modflow_grid.get_lrc(cell)
                    if isinstance(lrc, list) and len(lrc) > 0:
                        l, r, c = lrc[0]
                    else:
                        raise ValueError(f"Unexpected return from get_lrc(cell): {lrc}")
                else:
                    l = modflow_grid.get_layer(cell)
                    r, c = None, None

                layers.append(l)
                rows.append(r if r is not None else -1)
                cols.append(c if c is not None else -1)

        return gpd.GeoDataFrame({
            'layer': layers,
            'row': rows,
            'column': cols,
            'geometry': modflow_polygons
        }, crs=grid_crs)
