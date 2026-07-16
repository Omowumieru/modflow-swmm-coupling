"""
SWMM run management for MODFLOW-SWMM coupling.

This module handles SWMM simulation runs, step advancement, and data extraction.
"""

import logging
from typing import Dict, Any
from pyswmm import Subcatchments


class SWMMRunManager:
    """Manages SWMM simulation runs and step advancement."""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.swmm_hour = 0
        self.swmm_day = 0
        self.daily_max_infil_vol_records = []  
        self.daily_water_elev_records = []     

    def advance_swmm_to_stress_period(self, swmm_sim, target_hour: int) -> int:
        """
        Advance SWMM simulation to the target hour for a stress period.
        
        Args:
            swmm_sim: SWMM simulation object
            target_hour: Target hour to advance to
            
        Returns:
            Number of hours advanced
        """
        hours_advanced = 0
        
        if self.swmm_hour < target_hour - 1: 
            hours_to_advance = target_hour - 1 - self.swmm_hour 
            self.logger.debug(f"  Advancing SWMM by {hours_to_advance} hours to reach hour {target_hour}")
            
            for _ in range(hours_to_advance):
                try:
                    next(swmm_sim)
                    self.swmm_hour += 1
                    hours_advanced += 1
                except StopIteration:
                    self.logger.warning(f"  SWMM simulation ended while advancing to hour {target_hour}")
                    break
        
        return hours_advanced

    def run_swmm_24_hours(self, swmm_sim, current_stress_period_day: int,
                         start_hour: int, end_hour: int, data=None) -> Dict[str, Any]:
        """Run SWMM for 24 hours. Returns empty result dicts on failure."""
        try:
            self.logger.info(f"  Running SWMM for day {current_stress_period_day} (hours {start_hour}-{end_hour})...")

            # Track daily infiltration and evaporation using INSTANTANEOUS method
            daily_infiltration_by_subcatchment = {}
            daily_evaporation_by_subcatchment = {}

            # Keep statistics method for other variables (precipitation, runoff)
            daily_precipitation_by_subcatchment = {}
            daily_runoff_by_subcatchment = {}
            initial_stats_by_subcatchment = {}

            # Initialize subcatchment tracking
            for sub in Subcatchments(swmm_sim):
                sub_id = str(sub.subcatchmentid)
                daily_infiltration_by_subcatchment[sub_id] = 0.0
                daily_evaporation_by_subcatchment[sub_id] = 0.0
                daily_precipitation_by_subcatchment[sub_id] = 0.0
                daily_runoff_by_subcatchment[sub_id] = 0.0

                if hasattr(sub, 'statistics') and sub.statistics:
                    initial_stats_by_subcatchment[sub_id] = {
                        'precipitation': sub.statistics.get('precipitation', 0.0),
                        'runoff': sub.statistics.get('runoff', 0.0)
                    }
                else:
                    initial_stats_by_subcatchment[sub_id] = {
                        'precipitation': 0.0,
                        'runoff': 0.0
                    }

            # === 24-HOUR SIMULATION LOOP WITH INSTANTANEOUS COLLECTION ===
            for hour in range(24):
                try:
                    if self.is_swmm_simulation_complete(swmm_sim):
                        self.logger.info(f"    SWMM simulation reached its end time at hour {self.swmm_hour}.")
                        break

                    next(swmm_sim)
                    self.swmm_hour += 1

                    # === COLLECT INSTANTANEOUS INFILTRATION AND EVAPORATION RATES ===
                    for sub in Subcatchments(swmm_sim):
                        sub_id = str(sub.subcatchmentid)
                        try:
                            hourly_infil_rate = sub.infiltration_loss
                            hourly_infil_depth = hourly_infil_rate * 1.0  # inches for this hour
                            daily_infiltration_by_subcatchment[sub_id] += hourly_infil_depth

                            daily_evap_rate = sub.evaporation_loss  # in/day
                            hourly_evap_rate = daily_evap_rate / 24.0
                            daily_evaporation_by_subcatchment[sub_id] += hourly_evap_rate

                            if sub_id in ['1', '2', '3'] and hour < 3:
                                self.logger.debug(f"    Hour {hour+1} Sub {sub_id}: infiltration_loss={hourly_infil_rate:.6f} in/hr, evaporation_loss={daily_evap_rate:.6f} in/day -> hourly_evap={hourly_evap_rate:.6f} in/hr")

                        except Exception as e:
                            self.logger.warning(f"    Error getting infiltration/evaporation for subcatchment {sub_id}: {e}")

                except StopIteration:
                    self.logger.info(f"    SWMM simulation completed successfully at hour {self.swmm_hour + 1}.")
                    break

            # === CALCULATE PRECIPITATION, RUNOFF USING STATISTICS ===
            current_stats_by_subcatchment = {}
            for sub in Subcatchments(swmm_sim):
                sub_id = str(sub.subcatchmentid)
                if hasattr(sub, 'statistics') and sub.statistics:
                    current_stats_by_subcatchment[sub_id] = sub.statistics.copy()

            for sub_id, current_stats in current_stats_by_subcatchment.items():
                if hasattr(self, 'previous_period_stats') and sub_id in self.previous_period_stats:
                    previous_stats = self.previous_period_stats[sub_id]
                else:
                    previous_stats = initial_stats_by_subcatchment.get(sub_id, {})

                period_precipitation = current_stats.get('precipitation', 0.0) - previous_stats.get('precipitation', 0.0)
                period_runoff = current_stats.get('runoff', 0.0) - previous_stats.get('runoff', 0.0)

                daily_precipitation_by_subcatchment[sub_id] = period_precipitation
                daily_runoff_by_subcatchment[sub_id] = period_runoff

            # Store current stats for next period
            self.previous_period_stats = current_stats_by_subcatchment

            # Use existing subcatchment areas from data container
            total_area_m2 = 0.0
            if data and hasattr(data, 'swmm_subcatchment_areas'):
                total_area_m2 = sum(data.swmm_subcatchment_areas.values())
                self.logger.debug(f"  Total subcatchment area: {total_area_m2:.2f} m²")
            else:
                self.logger.warning("  No subcatchment areas available in data container")

            # Calculate daily precipitation
            total_daily_precip_m3_per_day = 0.0
            for sub_id in daily_precipitation_by_subcatchment:
                if data and hasattr(data, 'swmm_subcatchment_areas') and sub_id in data.swmm_subcatchment_areas:
                    subcatchment_area_m2 = data.swmm_subcatchment_areas[sub_id]
                    precip_inches = daily_precipitation_by_subcatchment[sub_id]
                    precip_m = precip_inches * 0.0254
                    subcatchment_volume_m3_per_day = precip_m * subcatchment_area_m2
                    total_daily_precip_m3_per_day += subcatchment_volume_m3_per_day

            # Calculate daily runoff
            total_daily_runoff_m3_per_day = 0.0
            if data and hasattr(data, 'swmm_subcatchment_areas'):
                for sub_id, daily_runoff_inches in daily_runoff_by_subcatchment.items():
                    if sub_id in data.swmm_subcatchment_areas:
                        subcatchment_area_m2 = data.swmm_subcatchment_areas[sub_id]
                        subcatchment_runoff_volume_m3_per_day = daily_runoff_inches * 25.4 * 0.001 * subcatchment_area_m2
                        total_daily_runoff_m3_per_day += subcatchment_runoff_volume_m3_per_day
            else:
                total_daily_precip_inches = sum(daily_precipitation_by_subcatchment.values())
                total_daily_precip_m3_per_day = total_daily_precip_inches * 25.4 * 0.001 * total_area_m2
                total_daily_runoff_inches = sum(daily_runoff_by_subcatchment.values())
                total_daily_runoff_m3_per_day = total_daily_runoff_inches * 25.4 * 0.001 * total_area_m2
                self.logger.warning("  Using fallback calculation (total area) - individual subcatchment areas not available")

            self.logger.info(f"  Daily precipitation: {sum(daily_precipitation_by_subcatchment.values()):.6f} inches, {total_daily_precip_m3_per_day:.6f} m³/day")

            total_runoff = sum(daily_runoff_by_subcatchment.values())
            self.logger.debug(f"  Daily runoff: {total_runoff:.6f} inches, {total_daily_runoff_m3_per_day:.6f} m³/day")

            # Calculate total daily evaporation
            total_daily_evaporation_m3_per_day = 0.0
            if hasattr(data, 'swmm_subcatchment_areas') and data.swmm_subcatchment_areas:
                for sub_id, daily_evaporation_inches in daily_evaporation_by_subcatchment.items():
                    subcatchment_area_m2 = data.swmm_subcatchment_areas.get(sub_id, 0.0)
                    if subcatchment_area_m2 > 0:
                        subcatchment_evaporation_volume_m3_per_day = daily_evaporation_inches * 25.4 * 0.001 * subcatchment_area_m2
                        total_daily_evaporation_m3_per_day += subcatchment_evaporation_volume_m3_per_day
            else:
                total_daily_evaporation_inches = sum(daily_evaporation_by_subcatchment.values())
                total_daily_evaporation_m3_per_day = total_daily_evaporation_inches * 25.4 * 0.001 * total_area_m2

            self.logger.debug(f"  Daily evaporation: {sum(daily_evaporation_by_subcatchment.values()):.6f} inches, {total_daily_evaporation_m3_per_day:.6f} m³/day")

            # Log individual subcatchment precipitation (first few)
            for sub_id, precip_inches in list(daily_precipitation_by_subcatchment.items())[:5]:
                self.logger.debug(f"    Subcatchment {sub_id}: {precip_inches:.6f} inches")

            try:
                for sub in Subcatchments(swmm_sim):
                    sub_id = str(sub.subcatchmentid)
                    gw = sub.gw_state
                    if gw is None:
                        continue
                    self.daily_max_infil_vol_records.append({
                        'day': current_stress_period_day,
                        'subcatchment_id': sub_id,
                        'max_infil_volume': gw['max_infil_volume']  # ft³
                    })
                    self.daily_water_elev_records.append({
                        'day': current_stress_period_day,
                        'subcatchment_id': sub_id,
                        'gwt_elev': gw['gwt_elev']  # ft
                    })
            except Exception as e:
                self.logger.warning(f"  Could not collect gw_state snapshots: {e}")

            return {
                'daily_infiltration_by_subcatchment': daily_infiltration_by_subcatchment,
                'daily_precipitation_by_subcatchment': daily_precipitation_by_subcatchment,
                'daily_runoff_by_subcatchment': daily_runoff_by_subcatchment,
                'daily_evaporation_by_subcatchment': daily_evaporation_by_subcatchment,
                'total_daily_precip_m3_per_day': total_daily_precip_m3_per_day,
                'total_daily_runoff_m3_per_day': total_daily_runoff_m3_per_day,
                'total_daily_evaporation_m3_per_day': total_daily_evaporation_m3_per_day,
                'total_precip': sum(daily_precipitation_by_subcatchment.values()),
                'total_runoff': sum(daily_runoff_by_subcatchment.values()),
                'total_infil': sum(daily_infiltration_by_subcatchment.values()),
                'hours_completed': 24
            }

        except Exception as e:
            self.logger.error(f"  Error running SWMM for 24 hours: {e}")
            self.logger.warning("  Using empty infiltration data")
            return {
                'daily_infiltration_by_subcatchment': {},
                'daily_precipitation_by_subcatchment': {},
                'daily_runoff_by_subcatchment': {},
                'daily_evaporation_by_subcatchment': {},
                'total_daily_precip_m3_per_day': 0.0,
                'total_daily_runoff_m3_per_day': 0.0,
                'total_daily_evaporation_m3_per_day': 0.0,
                'total_precip': 0.0,
                'total_runoff': 0.0,
                'total_infil': 0.0,
                'hours_completed': 0
            }

    def finalize_swmm_simulation(self, swmm_sim):
        """Close the SWMM simulation to ensure all report files are written correctly."""
        try:
            swmm_sim.close()
            self.logger.info("SWMM simulation closed and reports finalized.")
        except Exception as e:
            self.logger.error(f"Error during SWMM finalization: {e}")

    def is_swmm_simulation_complete(self, swmm_sim):
        """Check if the SWMM simulation has reached its configured end time."""
        try:
            time_remaining = (swmm_sim.end_time - swmm_sim.current_time).total_seconds()
            return time_remaining < 1
        except Exception as e:
            self.logger.error(f"Error checking SWMM simulation completion: {e}")
            return False

    def save_subcatchment_gw_state_csvs(self, output_dir: str = "results/simulation", scenario: str = "baseline") -> None:
        """Export daily end-of-day gw_state snapshots to pivoted CSVs.

        Filenames get a `_<scenario>` suffix so baseline and SLR runs don't clobber.
        """
        import pandas as pd
        import os
        os.makedirs(output_dir, exist_ok=True)

        if self.daily_max_infil_vol_records:
            df = pd.DataFrame(self.daily_max_infil_vol_records)
            pivot = df.pivot(index='day', columns='subcatchment_id', values='max_infil_volume')
            path = os.path.join(output_dir, f"subcatchment_max_infil_vol_{scenario}.csv")
            pivot.to_csv(path)
            self.logger.info(f"Saved max_infil_volume CSV → {path}")

        if self.daily_water_elev_records:
            df = pd.DataFrame(self.daily_water_elev_records)
            pivot = df.pivot(index='day', columns='subcatchment_id', values='gwt_elev')
            path = os.path.join(output_dir, f"subcatchment_gwt_elev_ft_{scenario}.csv")
            pivot.to_csv(path)
            self.logger.info(f"Saved gwt_elev CSV → {path}")