"""
SWMM object classifier — separates sanitary vs stormwater nodes/conduits/outfalls.

Uses the [DWF] (Dry Weather Flow) section as the authoritative sanitary seed,
then propagates the classification through the conduit topology. Falls back to
empty sanitary sets when [DWF] is absent (stormwater-only model).

This replaces the brittle prefix-based detection ('M', 'SO') used previously.
"""

import contextlib
import io
import warnings
from typing import Dict, Set

import swmmio


@contextlib.contextmanager
def _quiet_swmmio():
    """Suppress swmmio's stdout chatter (e.g. '.rpt failed to initialize') and pandas FutureWarnings."""
    buf = io.StringIO()
    with warnings.catch_warnings(), contextlib.redirect_stdout(buf):
        warnings.simplefilter("ignore", FutureWarning)
        yield


def classify_swmm_objects(inp_path: str) -> Dict[str, Set[str]]:
    """
    Classify nodes, conduits, and outfalls in a SWMM .inp file as sanitary or stormwater.

    Algorithm:
      1. Seed sanitary nodes from the [DWF] section (nodes with dry-weather flow).
      2. Build an undirected graph of conduit connections.
      3. Flood-fill from the seeds — every node reachable along conduits becomes sanitary.
      4. A conduit is sanitary iff both endpoints are sanitary.
      5. A sanitary outfall is an outfall in the sanitary set (the terminus of the network).

    Returns a dict with set-of-IDs for each category. Sets, not lists, so lookups are O(1).
    """
    with _quiet_swmmio():
        model = swmmio.Model(str(inp_path))
        junctions = set(model.inp.junctions.index)
        outfalls = set(model.inp.outfalls.index)
        try:
            dwf_seeds_raw = set(model.inp.dwf.index)
        except Exception:
            dwf_seeds_raw = set()
        conduits = model.inp.conduits

    all_nodes = junctions | outfalls
    dwf_seeds = dwf_seeds_raw & all_nodes
    all_conduits = set(conduits.index)

    adj: Dict[str, Set[str]] = {n: set() for n in all_nodes}
    for _, row in conduits.iterrows():
        a, b = row['InletNode'], row['OutletNode']
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)

    sanitary_nodes: Set[str] = set()
    stack = list(dwf_seeds)
    while stack:
        n = stack.pop()
        if n in sanitary_nodes:
            continue
        sanitary_nodes.add(n)
        stack.extend(adj[n] - sanitary_nodes)

    sanitary_conduits = {
        cname for cname, row in conduits.iterrows()
        if row['InletNode'] in sanitary_nodes and row['OutletNode'] in sanitary_nodes
    }
    sanitary_outfalls = outfalls & sanitary_nodes

    return {
        'sanitary_nodes': sanitary_nodes,
        'sanitary_conduits': sanitary_conduits,
        'sanitary_outfalls': sanitary_outfalls,
        'stormwater_nodes': all_nodes - sanitary_nodes,
        'stormwater_conduits': all_conduits - sanitary_conduits,
        'stormwater_outfalls': outfalls - sanitary_outfalls,
        'all_junctions': junctions,
        'all_outfalls': outfalls,
        'all_nodes': all_nodes,
        'all_conduits': all_conduits,
        'dwf_seeds': dwf_seeds,
    }
