import pandas as pd
import logging

def calculate_swmm_modflow_proportions(gdf_grid, gdf_subcatchments, layer, conversion_factor=4046.85642):
    """
    Calculates the proportion of each SWMM subcatchment that overlaps with each MODFLOW grid cell.
    
    Parameters:
    gdf_grid (GeoDataFrame): GeoDataFrame of MODFLOW grid cells with geometry, row, and column.
    gdf_subcatchments (GeoDataFrame): GeoDataFrame of SWMM subcatchments with geometry and area (in acres).
    conversion_factor (float): Conversion factor to convert acres to square meters (default is 4046.85642).
    layer (int): MODFLOW layer number.

    Returns:
    DataFrame: A DataFrame with the calculated proportions for each intersection between MODFLOW grid cells and SWMM subcatchments.
    """
    results = []
    gdf_grid['bbox'] = gdf_grid.geometry.apply(lambda x: x.bounds)  # Create bounding box for each grid cell
    grid_sindex = gdf_grid.sindex  # Create a spatial index for the grid cells for faster intersection checks

    # Store proportion totals per subcatchment for validation
    subcatchment_totals = {}

    # Iterate over each subcatchment
    for _, subcatchment in gdf_subcatchments.iterrows():
        subcatchment_geom = subcatchment.geometry
        subcatchment_area_acres = subcatchment.Area  # 'Area' is in acres
        subcatchment_area_sqm = subcatchment_area_acres * conversion_factor  # Convert to square meters
        subcatchment_id = str(subcatchment['id'])  # Ensure ID is stored as string

        # Validate subcatchment geometry and area
        if subcatchment_area_acres <= 0:
            logging.warning(f"WARNING: Subcatchment {subcatchment_id} has zero or negative area: {subcatchment_area_acres} acres")
            continue
            
        if not subcatchment_geom.is_valid:
            logging.warning(f"WARNING: Subcatchment {subcatchment_id} has invalid geometry")
            subcatchment_geom = subcatchment_geom.buffer(0)  # Try to fix geometry

        # Find potential grid cells that intersect with the subcatchment
        possible_matches_index = list(grid_sindex.intersection(subcatchment_geom.bounds))
        possible_matches = gdf_grid.iloc[possible_matches_index]

        # Store temporary results for the current subcatchment
        temp_results = []
        total_intersection_area = 0.0

        # Perform precise intersection with filtered grid cells
        for _, cell in possible_matches.iterrows():
            try:
                intersection = subcatchment_geom.intersection(cell.geometry)

                if not intersection.is_empty and intersection.area > 0:
                    intersection_area = intersection.area
                    modflow_area = cell.geometry.area
                    
                    # Validate areas
                    if modflow_area <= 0:
                        logging.warning(f"WARNING: MODFLOW cell ({cell['row']}, {cell['column']}) has zero area")
                        continue
                    
                    # Calculate proportions
                    proportion_in_modflow = intersection_area / modflow_area  # Fraction of MODFLOW cell covered by subcatchment
                    proportion_in_swmm = intersection_area / subcatchment_area_sqm  # Fraction of subcatchment covered by MODFLOW cell
                    
                    # Validate proportions
                    if proportion_in_modflow > 1.0:
                        logging.warning(f"WARNING: Cell ({cell['row']}, {cell['column']}) proportion_in_modflow > 1.0: {proportion_in_modflow:.6f}")
                        proportion_in_modflow = min(proportion_in_modflow, 1.0)
                    
                    if proportion_in_swmm > 1.0:
                        logging.warning(f"WARNING: Cell ({cell['row']}, {cell['column']}) proportion_in_swmm > 1.0: {proportion_in_swmm:.6f}")
                        proportion_in_swmm = min(proportion_in_swmm, 1.0)
                    
                    row = cell['row']
                    col = cell['column']

                    temp_results.append({
                        'subcatchment_id': subcatchment_id,
                        'subcatchment_area': subcatchment_area_sqm,
                        'layer': layer,
                        'row': row,
                        'col': col,
                        'proportion_in_modflow': proportion_in_modflow,
                        'proportion_in_swmm': proportion_in_swmm,
                        'intersection_area': intersection_area
                    })
                    
                    total_intersection_area += intersection_area
                    
            except Exception as e:
                logging.error(f"ERROR: Failed to calculate intersection for subcatchment {subcatchment_id}, cell ({cell['row']}, {cell['column']}): {e}")
                continue

        # Calculate total proportion before normalization
        total_proportion = sum(res['proportion_in_swmm'] for res in temp_results)
        
        logging.debug(f"Subcatchment ID: {subcatchment_id}")
        logging.debug(f"  Total area: {subcatchment_area_acres:.4f} acres ({subcatchment_area_sqm:.2f} m²)")
        logging.debug(f"  Total intersection area: {total_intersection_area:.2f} m²")
        logging.debug(f"  Total proportion before normalization: {total_proportion:.6f}")
        logging.debug(f"  Coverage percentage: {(total_intersection_area/subcatchment_area_sqm)*100:.2f}%")

        # Validate and normalize proportions
        if total_proportion > 0:
            # Check if normalization is needed (should be close to 1.0)
            if abs(total_proportion - 1.0) > 0.001:
                logging.debug(f"  Proportions don't sum to 1.0, normalizing from {total_proportion:.6f} to 1.0")

                # Normalize proportions to sum to 1.0
                for res in temp_results:
                    res['proportion_in_swmm'] /= total_proportion

                # Verify normalization
                normalized_total = sum(res['proportion_in_swmm'] for res in temp_results)
                logging.debug(f"  Normalized total: {normalized_total:.6f}")
            else:
                logging.debug(f"  Proportions sum correctly to 1.0")
            
            # Store the total for validation
            subcatchment_totals[subcatchment_id] = sum(res['proportion_in_swmm'] for res in temp_results)
        else:
            logging.warning(f"  WARNING: No valid intersections found for subcatchment {subcatchment_id}")
            subcatchment_totals[subcatchment_id] = 0.0

        results.extend(temp_results)

    # Convert the results to a DataFrame and ensure types are consistent
    results_df = pd.DataFrame(results)

    # Enforce correct data type for subcatchment_id, row, col
    if not results_df.empty:
        results_df['subcatchment_id'] = results_df['subcatchment_id'].astype(str)
        results_df['row'] = results_df['row'].astype(int)
        results_df['col'] = results_df['col'].astype(int)
        
        # Final validation summary
        logging.info(
            f"Proportioning matrix: {len(results_df)} records, "
            f"{results_df['subcatchment_id'].nunique()} subcatchments, "
            f"{len(results_df[['row', 'col']].drop_duplicates())} cells"
        )

        # Check proportion ranges (debug only)
        modflow_props = results_df['proportion_in_modflow']
        swmm_props = results_df['proportion_in_swmm']
        logging.debug(
            f"Proportion ranges — modflow: {modflow_props.min():.6f}-{modflow_props.max():.6f}, "
            f"swmm: {swmm_props.min():.6f}-{swmm_props.max():.6f}"
        )

        # Verify subcatchment totals — only WARN on real deviations
        for sub_id, total in subcatchment_totals.items():
            if abs(total - 1.0) > 0.001 and total > 0:
                logging.warning(f"  Subcatchment {sub_id} final total: {total:.6f} (should be 1.0)")
    else:
        logging.warning("WARNING: No results generated!")

    return results_df
