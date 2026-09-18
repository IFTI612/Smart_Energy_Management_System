import pulp
from typing import List, Tuple
from app.schemas import HourlyData, BatterySpec, DirectiveInterpretation, HourlyPlanItem, BatteryAction, DirectiveType

class InfeasiblePlanException(Exception):
    pass

def solve_energy_plan(scenario_id: str, hours: List[HourlyData], battery: BatterySpec, directives: List[DirectiveInterpretation]) -> Tuple[List[HourlyPlanItem], float, float, float, str]:
    prob = pulp.LpProblem("Energy_Optimization", pulp.LpMinimize)
    
    # Decision variables
    grid = {i: pulp.LpVariable(f"grid_{i}", lowBound=0) for i in range(24)}
    solar_used = {i: pulp.LpVariable(f"solar_used_{i}", lowBound=0) for i in range(24)}
    charge = {i: pulp.LpVariable(f"charge_{i}", lowBound=0) for i in range(24)}
    discharge = {i: pulp.LpVariable(f"discharge_{i}", lowBound=0) for i in range(24)}
    energy_after = {i: pulp.LpVariable(f"energy_after_{i}", lowBound=0) for i in range(24)}
    
    # Process directives
    solar_factors = {h: 1.0 for h in range(24)}
    min_reserves = {h: 0.0 for h in range(24)}
    
    no_charge_hours = set()
    no_discharge_hours = set()
    max_grid_limits = {}
    
    for d in directives:
        if not d.applies or not d.structured_adjustment:
            continue
        dh = d.structured_adjustment.get("hours", [])
        if d.directive_type == DirectiveType.solar_reduction:
            factor = d.structured_adjustment.get("factor", 1.0)
            for h in dh:
                solar_factors[h] *= factor
        elif d.directive_type == DirectiveType.minimum_battery_reserve:
            m = d.structured_adjustment.get("minimum_energy_kwh", 0.0)
            for h in dh:
                min_reserves[h] = max(min_reserves[h], m)
        elif d.directive_type == DirectiveType.no_charge_window:
            no_charge_hours.update(dh)
        elif d.directive_type == DirectiveType.no_discharge_window:
            no_discharge_hours.update(dh)
        elif d.directive_type == DirectiveType.max_grid_window:
            mg = d.structured_adjustment.get("max_grid_kwh", float('inf'))
            for h in dh:
                if h in max_grid_limits:
                    max_grid_limits[h] = min(max_grid_limits[h], mg)
                else:
                    max_grid_limits[h] = mg
                    
    # Constraints
    for h_data in hours:
        h = h_data.hour
        
        # Energy balance
        prob += grid[h] + solar_used[h] + discharge[h] == h_data.demand_kwh + charge[h], f"Balance_{h}"
        
        # Solar limit
        effective_solar = h_data.solar_kwh * solar_factors[h]
        prob += solar_used[h] <= effective_solar, f"SolarLimit_{h}"
        
        # Storage bounds
        lb = max(battery.minimum_energy_kwh, min_reserves[h])
        prob += energy_after[h] >= lb, f"MinReserve_{h}"
        prob += energy_after[h] <= battery.capacity_kwh, f"MaxCap_{h}"
        
        # Dynamics
        if h == 0:
            prob += energy_after[0] == battery.initial_energy_kwh + charge[0] - discharge[0], "Dyn_0"
        else:
            prob += energy_after[h] == energy_after[h-1] + charge[h] - discharge[h], f"Dyn_{h}"
            
        # Rate limits
        prob += charge[h] <= battery.max_charge_kwh_per_hour, f"MaxCharge_{h}"
        prob += discharge[h] <= battery.max_discharge_kwh_per_hour, f"MaxDischarge_{h}"
        
        # Directives
        if h in no_charge_hours:
            prob += charge[h] == 0, f"NoCharge_{h}"
        if h in no_discharge_hours:
            prob += discharge[h] == 0, f"NoDischarge_{h}"
        if h in max_grid_limits:
            prob += grid[h] <= max_grid_limits[h], f"MaxGrid_{h}"
            
    # End-of-day neutrality
    prob += energy_after[23] == battery.initial_energy_kwh, "EndNeutrality"
    
    # Objective
    tariff_dict = {h_data.hour: h_data.tariff_bdt_per_kwh for h_data in hours}
    prob += pulp.lpSum([grid[h] * tariff_dict[h] for h in range(24)]), "Total_Cost"
    
    # Solve
    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)
    
    if pulp.LpStatus[prob.status] != "Optimal":
        raise InfeasiblePlanException(f"Could not find an optimal solution. Status: {pulp.LpStatus[prob.status]}")
        
    # Extraction
    hourly_plan = []
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0
    
    active_summary_parts = []
    if no_charge_hours:
        active_summary_parts.append(f"No charge for {len(no_charge_hours)} hours")
    if no_discharge_hours:
        active_summary_parts.append(f"No discharge for {len(no_discharge_hours)} hours")
    if max_grid_limits:
        active_summary_parts.append("Grid limits applied")
        
    for h in range(24):
        g_val = pulp.value(grid[h]) or 0.0
        s_val = pulp.value(solar_used[h]) or 0.0
        c_val = pulp.value(charge[h]) or 0.0
        d_val = pulp.value(discharge[h]) or 0.0
        e_val = pulp.value(energy_after[h]) or 0.0
        
        c_rounded = round(c_val, 4)
        d_rounded = round(d_val, 4)
        
        if c_rounded > 1e-4:
            action = BatteryAction.charge
            bat_kwh = c_rounded
        elif d_rounded > 1e-4:
            action = BatteryAction.discharge
            bat_kwh = d_rounded
        else:
            action = BatteryAction.idle
            bat_kwh = 0.0
            
        total_grid += g_val
        total_cost += g_val * tariff_dict[h]
        if g_val > peak_grid:
            peak_grid = g_val
            
        hourly_plan.append(HourlyPlanItem(
            hour=h,
            grid_kwh=round(g_val, 4),
            solar_used_kwh=round(s_val, 4),
            battery_action=action,
            battery_kwh=bat_kwh,
            battery_energy_after_kwh=round(e_val, 4)
        ))
        
    total_grid = round(total_grid, 4)
    total_cost = round(total_cost, 4)
    peak_grid = round(peak_grid, 4)
    
    directives_count = sum(1 for d in directives if d.applies)
    
    summary = f"Plan generated with {directives_count} active directives. Total cost: {total_cost} BDT. Peak grid: {peak_grid} kWh."
    if active_summary_parts:
        summary += " Constraints applied: " + ", ".join(active_summary_parts) + "."
        
    return hourly_plan, total_grid, total_cost, peak_grid, summary
