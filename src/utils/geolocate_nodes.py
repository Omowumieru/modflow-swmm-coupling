import geopandas as gpd
import csv
import os

def map_nodes_to_modflow_grid_by_coordinates(modflow_grid_gdf, nodes_shp_filepath, xmin, xmax, ymin, ymax, delr, delc, sanitary_nodes=None):
    """
    Maps SWMM nodes (from a shapefile) to MODFLOW grid cells based on their X and Y coordinates.
    If multiple nodes are in the same cell, they are stored as a list.

    Parameters:
    -----------
    modflow_grid_gdf : GeoDataFrame
        MODFLOW grid cells as polygons, with 'row' and 'column' attributes.
    nodes_shp_filepath : str
        Filepath to the shapefile containing SWMM nodes with 'X' and 'Y' coordinates.
    xmin, xmax : float
        Minimum and maximum X coordinates of the MODFLOW grid.
    ymin, ymax : float
        Minimum and maximum Y coordinates of the MODFLOW grid.
    delr, delc : float
        Grid cell size in the X and Y directions.

    Returns:
    --------
    tuple:
        - Updated `modflow_grid_gdf` with assigned SWMM nodes.
        - Dictionary `modflow_to_swmm_mapping` linking (row, col) to SWMM node names.
    """
    
    # Load SWMM nodes from shapefile
    gdf_nodes = gpd.read_file(nodes_shp_filepath)
    
    # Extract relevant fields and avoid SettingWithCopyWarning
    nodes_df = gdf_nodes[['NAME', 'X', 'Y']].copy()

    # Get grid dimensions from the MODFLOW grid
    nrow = modflow_grid_gdf['row'].max() + 1
    ncol = modflow_grid_gdf['column'].max() + 1
    
   
    
    # Define function to map coordinates to MODFLOW grid row and column
    def map_to_modflow_grid(x, y, xmin, ymax, delr, delc):
        # Calculate column: (x - xmin) / delr
        col = int((x - xmin) / delr)
        # Calculate row: (ymax - y) / delc (MODFLOW rows start from top)
        row = int((ymax - y) / delc)
        return row, col

    # Compute MODFLOW row and column for each SWMM node
    nodes_df['modflow_row'], nodes_df['modflow_col'] = zip(*nodes_df.apply(
        lambda row: map_to_modflow_grid(row['X'], row['Y'], xmin, ymax, delr, delc), axis=1))

    # Validate grid bounds
    out_of_bounds = []
    valid_nodes = []
    
    for _, node in nodes_df.iterrows():
        row, col = node['modflow_row'], node['modflow_col']
        node_name = node['NAME']
        
        if row < 0 or row >= nrow or col < 0 or col >= ncol:
            out_of_bounds.append({
                'node': node_name,
                'coordinates': (node['X'], node['Y']),
                'grid_cell': (row, col),
                'bounds': (nrow, ncol)
            })
        else:
            valid_nodes.append(node)

    # Report out-of-bounds nodes
    if out_of_bounds:
        print(f"\nWARNING: {len(out_of_bounds)} nodes are outside MODFLOW grid bounds:")
        for item in out_of_bounds[:10]:  # Show first 10
            print(f"  Node {item['node']}: coords ({item['coordinates'][0]:.2f}, {item['coordinates'][1]:.2f}) -> cell {item['grid_cell']} (bounds: {item['bounds']})")
        if len(out_of_bounds) > 10:
            print(f"  ... and {len(out_of_bounds) - 10} more")
    
    print(f"\nValid nodes within grid bounds: {len(valid_nodes)}")

    # Initialize 'node_name' column in MODFLOW grid GeoDataFrame as empty lists
    modflow_grid_gdf['node_name'] = [[] for _ in range(len(modflow_grid_gdf))]  
    
    # Create the dictionary mapping MODFLOW cells to SWMM nodes
    modflow_to_swmm_mapping = {}

    # Assign SWMM nodes to corresponding MODFLOW grid cells (only valid ones)
    for node in valid_nodes:
        row, col = node['modflow_row'], node['modflow_col']
        node_name = node['NAME']

        # Find the matching MODFLOW grid cell
        cell = modflow_grid_gdf.loc[
            (modflow_grid_gdf['row'] == row) & 
            (modflow_grid_gdf['column'] == col), 'node_name'
        ]

        # Append the node name if the cell exists
        if not cell.empty:
            modflow_grid_gdf.at[cell.index[0], 'node_name'].append(node_name)
        
        # Add to mapping dictionary
        if (row, col) not in modflow_to_swmm_mapping:
            modflow_to_swmm_mapping[(row, col)] = []
        modflow_to_swmm_mapping[(row, col)].append(node_name)

    # Summary statistics
    cells_with_nodes = len([cell for cell in modflow_grid_gdf['node_name'] if cell])
    total_nodes_mapped = sum(len(cell) for cell in modflow_grid_gdf['node_name'])
    
    print(f"\nMapping Summary:")
    print(f"  Total nodes processed: {len(nodes_df)}")
    print(f"  Valid nodes mapped: {len(valid_nodes)}")
    print(f"  MODFLOW cells with nodes: {cells_with_nodes}")
    print(f"  Total node-cell mappings: {total_nodes_mapped}")

    # --- Export sanitary node info to CSV ---
    sanitary_set = set(sanitary_nodes) if sanitary_nodes is not None else set()
    results_dir = 'results'
    os.makedirs(results_dir, exist_ok=True)
    sanitary_node_locations = []
    layer = 0  # Assuming all nodes are in layer 0; adjust if needed
    for (row, col), node_list in modflow_to_swmm_mapping.items():
        for node_name in node_list:
            if node_name in sanitary_set:
                sanitary_node_locations.append((layer, row, col, node_name))

    if sanitary_node_locations:
        csv_path = os.path.join(results_dir, 'sanitary_nodes.csv')
        with open(csv_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['layer', 'row', 'col', 'node_name'])
            for entry in sanitary_node_locations:
                writer.writerow(entry)
        print(f"\nExported {len(sanitary_node_locations)} sanitary nodes to '{csv_path}'.")
    else:
        print("\nNo sanitary nodes found to export (classifier produced an empty set).")

    # Return the updated MODFLOW grid GeoDataFrame and the dictionary mapping
    return modflow_grid_gdf, modflow_to_swmm_mapping

