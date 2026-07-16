# MODFLOW-SWMM Coupling

Bidirectional coupling of **SWMM** (stormwater/sewer) and **MODFLOW 6** (groundwater)
for daily-timestep coastal-flood and sea-level-rise modeling. Originally developed
for Bowers Beach, Delaware.

Three exchanges happen every stress period (1 day):

- **SWMM → MODFLOW**: subcatchment infiltration becomes MODFLOW recharge
  (spatially proportioned to grid cells, lagged one day).
- **MODFLOW → SWMM**: groundwater drainage from coupled cells injected into
  SWMM junctions.
- **MODFLOW ↔ sanitary sewer nodes**: head-based leakage into pipes (M / SO
  prefixed nodes) via the MODFLOW WEL package.

## Installation

Groundwater coupling depends on a **custom fork of PySWMM and the SWMM
toolkit** — a vanilla `pyswmm` install from PyPI will not expose the
groundwater-coupled hooks. Both forks are public and are pinned (by commit)
in the conda environment file:

- PySWMM fork — <https://github.com/AustinFarnum/pyswmm>
- SWMM toolkit fork — <https://github.com/AustinFarnum/swmm-python>

**Recommended — reproduce the environment with conda:**

```bash
conda env create -f env/environment.yml   # creates the `pyflo` env
conda activate pyflo
```

This installs everything, including the two forks (via their `git+https` pins).
`env/environment.yml` is a curated, cross-platform file that conda solves
per-OS. A bit-for-bit export of the original macOS dev machine is kept in
`env/environment.macos.lock.yml` if you need to reproduce it exactly.

**Build prerequisite (important):** `swmm-toolkit` compiles the SWMM C engine
from source. The env file bundles a build toolchain (`cmake`, `swig`,
`c-compiler`, `cxx-compiler`), so on **macOS and Linux** the build is
self-contained. On **Windows** you must also install the
[Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
before creating the env.

> **Note:** `requirements.txt` lists the core packages for reference, but it
> pulls **vanilla** `pyswmm` and therefore will *not* enable the
> groundwater-coupled features. Use `env/environment.yml` for a working setup.

<details>
<summary><b>Fallback — install the SWMM forks manually</b> (if the env build fails)</summary>

If `conda env create` fails on the `swmm-toolkit` build, create/activate a base
env with the toolchain, then install the two forks by hand. **Order matters:**
install `swmm-toolkit` *before* `pyswmm`, otherwise pip pulls the vanilla
`swmm-toolkit` as a dependency and shadows the fork.

```bash
pip uninstall -y pyswmm swmm-toolkit    # clear any vanilla install first

# 1) SWMM toolkit fork FIRST (this is the step that compiles C code)
pip install "swmm-toolkit @ git+https://github.com/AustinFarnum/swmm-python.git#subdirectory=swmm-toolkit"

# 2) then the PySWMM fork
pip install "pyswmm @ git+https://github.com/AustinFarnum/pyswmm.git"
```

If a build says *"Failed to delete a temporary directory"*, the install
usually still succeeded — restart your shell/kernel and continue.
</details>

You also need the MODFLOW 6 shared library (`libmf6.dylib` / `.so` / `.dll`).
Pre-built binaries ship in `exe/`, or pass `--dll-path` to point elsewhere.
If you cloned a copy where `exe/` was excluded to keep the checkout small,
download MODFLOW 6 from the USGS
([modflow6 releases](https://github.com/MODFLOW-USGS/modflow6/releases)).

## Running a simulation

The code runs **one scenario per invocation**. Two scenarios are supported
out of the box:

| Scenario   | Constant Head Boundary (CHD)|
|------------|-----------------------------|
| `baseline` | 0.0 m                       |
| `slr`      | 1.2 m                       |

**Baseline run:**

```bash
python main.py run-simulation --scenario baseline
```

**SLR run** — you must manually edit the CHD value before running:

1. Open `modflow_ws/Bowers_beach.chd`
2. Change the head value from `0.0` to `1.2`
3. Run:
   ```bash
   python main.py run-simulation --scenario slr
   ```

All outputs get the scenario tag appended automatically
(e.g. `gw_head_differences_slr.csv`).

### Common flags

```
--scenario                 baseline | slr  (suffix on all output filenames)
--model-name               MODFLOW model name        (default: Bowers_beach)
--log-level                DEBUG | INFO | WARNING | ERROR   (default: INFO)
--dll-path                 path to libmf6.{dylib,so,dll}    (auto-detected by default)
--modflow-workspace        default: ./modflow_ws
--swmm-input               default: ./swmm_inp/Bowers_beach.inp
--subcatchments-shapefile  default: shp_files/delineation_shpfile.shp
--nodes-shapefile          default: shp_files/swmm_junc_out.shp
--well-csv                 sanitary-node CSV for the WEL package
                           (default: results/sanitary_nodes.csv)
--output-dir               override the default results directory
--no-validate-first        skip the pre-run setup validation (on by default)
```

Run `python main.py run-simulation --help` for the complete list, or
`python main.py validate-setup` to check the inputs without simulating.

```bash
python main.py run-simulation --scenario baseline > logs/baseline.log 2>&1 &
tail -f logs/baseline.log
```

## Outputs

```
results/
├── sanitary_nodes.csv              # input: node→cell mapping
├── simulation/                     # live, per-scenario outputs
│   ├── gw_head_differences_<scenario>.csv
│   ├── subcatchment_gwt_elev_ft_<scenario>.csv
│   ├── subcatchment_max_infil_vol_<scenario>.csv
│   ├── mean_water_table_<scenario>.tif
│   └── mean_groundwater_depth_map_<scenario>.png
├── preprocessing/                  # one-time grid/coupling artifacts
└── analysis/                       # zone budget, GIS
```

MODFLOW also writes `modflow_ws/*.lst`, `*.hds`, `*.cbc`. SWMM writes
`swmm_inp/Bowers_beach.rpt` and `.out`.

## Adapting to a new study area

The coupling logic is generic; the inputs are not. To use this for another site:

1. **Replace the MODFLOW model** in `modflow_ws/` and the SWMM `.inp` in
   `swmm_inp/`. Update `--model-name` if it isn't `Bowers_beach`.
2. **Replace the shapefiles** in `shp_files/`:
   - `delineation_shpfile.shp` — SWMM subcatchment polygons
   - `swmm_junc_out.shp` — SWMM junction/outfall points
3. **Generate the sanitary-node mapping**:
   ```bash
   python src/utils/geolocate_nodes.py
   ```
   This writes `results/sanitary_nodes.csv` — the canonical layer/row/col
   table for nodes that participate in groundwater–sewer leakage.
4. **Sanitary node naming convention**: nodes whose IDs start with `M` or
   `SO` are treated as sanitary at runtime. If your network uses different
   prefixes, search for `startswith(("M", "SO"))` in `src/core/well_manager.py`
   and update both occurrences (one in `compute_sanitary_node_infiltration`,
   one in `extract_invert_elevations`).
5. **Scenarios**: edit the CHD value (or add new scenarios) in
   `modflow_ws/Bowers_beach.chd` and pass any tag via `--scenario`.

## Project layout

```
modflow-swmm-coupling/
├── main.py                          # CLI entry point (Typer)
├── requirements.txt                 # core pip packages (reference; no forks)
├── env/
│   └── environment.yml              # full conda env `pyflo` (incl. forks)
├── src/
│   ├── coupled_simulation.py        # CoupledSimulation orchestrator
│   ├── postprocess.py               # results post-processing / maps
│   ├── core/                        # coupling components
│   │   ├── coupling_coordinator.py
│   │   ├── stress_period_manager.py
│   │   ├── surface_infiltration.py        # SWMM → MODFLOW recharge
│   │   ├── recharge_manager.py
│   │   ├── drainage_manager.py            # MODFLOW → SWMM drainage
│   │   ├── well_manager.py                # WEL package / sanitary leakage
│   │   ├── groundwater_elevation_manager.py
│   │   ├── modflow_api_helper.py
│   │   ├── swmm_run_manager.py
│   │   ├── data_container.py
│   │   ├── manager_factory.py
│   │   └── validation_manager.py
│   └── utils/                       # loaders, geolocation, proportions
│       ├── modflow_loader.py
│       ├── modflow_runner.py
│       ├── geolocate_nodes.py             # writes results/sanitary_nodes.csv
│       ├── swmm_classifier.py
│       └── swmm_modflow_proportions.py
├── modflow_ws/                      # MODFLOW 6 input/output workspace  *
├── swmm_inp/                        # SWMM input (.inp) + run outputs
├── shp_files/                       # subcatchment + node shapefiles    *
├── exe/                             # MODFLOW 6 binaries (win/mac/linux) *
├── tutorial/                        # Bowers_beach_tutorial.ipynb
└── results/                         # generated outputs (see "Outputs")

*  Large; captured in the initial snapshot commit but git-ignored afterward —
   see Installation for how to obtain them.
```

<!-- ## Citation

If you use this code, please cite:
*(BibTeX / CFF entry to be added — see `CITATION.cff`)*

## License

See `LICENSE`. -->
