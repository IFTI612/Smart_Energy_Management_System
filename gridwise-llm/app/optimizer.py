import pulp
import logging
from typing import List, Tuple, Dict, Any
from app.schemas import HourlyData, BatterySpec, DirectiveInterpretation, DirectiveType

class InfeasiblePlanException(Exception):
    pass

def solve_energy_plan(scenario_id: str,
                     hours: List[HourlyData],
                     battery: BatterySpec,
                     directives: List[DirectiveInterpretation]) -> Tuple[List[Dict[str, Any]], float, float, float, Dict[str, Any]]:
    prob = pulp.LpProblem("Energy_Optimization", pulp.LpMinimize)
    
    grid = {i: pulp.LpVariable(f"grid_{i}", lowBound=0) for i in range(24)}
    solar_used = {i: pulp.LpVariable(f"solar_used_{i}", lowBound=0) for i in range(24)}
    charge = {i: pulp.LpVariable(f"charge_{i}", lowBound=0) for i in range(24)}
    discharge = {i: pulp.LpVariable(f"discharge_{i}", lowBound=0) for i in range(24)}
    energy_after = {i: pulp.LpVariable(f"energy_after_{i}", lowBound=0) for i in range(24)}
    
    solar_factors = {h: 1.0 for h in range(24)}
    min_reserves = {h: battery.minimum_energy_kwh for h in range(24)}
    can_charge = {h: True for h in range(24)}
    can_discharge = {h: True for h in range(24)}
    max_grids = {h: None for h in range(24)}
    
    for d in directives:
        if not d.applies or d.structured_adjustment is None:
            continue
            
        target_hours = d.structured_adjustment.get("hours", [])
        if d.directive_type == DirectiveType.solar_reduction:
            f = d.structured_adjustment["factor"]
            for h in target_hours:
                solar_factors[h] = min(solar_factors[h], f)
        elif d.directive_type == DirectiveType.minimum_battery_reserve:
            r = d.structured_adjustment["minimum_energy_kwh"]
            for h in target_hours:
                min_reserves[h] = max(min_reserves[h], r)
        elif d.directive_type == DirectiveType.no_charge_window:
            for h in target_hours:
                can_charge[h] = False
        elif d.directive_type == DirectiveType.no_discharge_window:
            for h in target_hours:
                can_discharge[h] = False
        elif d.directive_type == DirectiveType.max_grid_window:
            mg = d.structured_adjustment["max_grid_kwh"]
            for h in target_hours:
                if max_grids[h] is None:
                    max_grids[h] = mg
                else:
                    max_grids[h] = min(max_grids[h], mg)
                    
    tariff_dict = {h.hour: h.tariff_bdt_per_kwh for h in hours}
    demand_dict = {h.hour: h.demand_kwh for h in hours}
    solar_dict = {h.hour: h.solar_kwh for h in hours}
    
    for h in range(24):
        avail_solar = solar_dict[h] * solar_factors[h]
        prob += solar_used[h] <= avail_solar
        
        c_upper = battery.max_charge_kwh_per_hour if can_charge[h] else 0.0
        d_upper = battery.max_discharge_kwh_per_hour if can_discharge[h] else 0.0
        prob += charge[h] <= c_upper
        prob += discharge[h] <= d_upper
        
        if max_grids[h] is not None:
            prob += grid[h] <= max_grids[h]
            
        prob += energy_after[h] >= min_reserves[h]
        prob += energy_after[h] <= battery.capacity_kwh
        
        if h == 0:
            prob += energy_after[h] == battery.initial_energy_kwh + charge[h] - discharge[h]
        else:
            prob += energy_after[h] == energy_after[h-1] + charge[h] - discharge[h]
            
        prob += grid[h] + solar_used[h] + discharge[h] == demand_dict[h] + charge[h]
        
    prob += energy_after[23] == battery.initial_energy_kwh
    
    prob += pulp.lpSum([grid[h] * tariff_dict[h] for h in range(24)]), "Total_Cost"
    
    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)
    
    if pulp.LpStatus[prob.status] != "Optimal":
        logging.error(f"Infeasible plan for scenario {scenario_id}. Solver status: {pulp.LpStatus[prob.status]}")
        raise InfeasiblePlanException()
        
    hourly_plan = []
    total_grid_kwh = 0.0
    total_cost_bdt = 0.0
    peak_grid_kwh = 0.0
    
    for h in range(24):
        g_val = grid[h].varValue or 0.0
        s_val = solar_used[h].varValue or 0.0
        c_val = charge[h].varValue or 0.0
        d_val = discharge[h].varValue or 0.0
        e_val = energy_after[h].varValue or 0.0
        
        action = "idle"
        if c_val > 1e-4:
            action = "charge"
        elif d_val > 1e-4:
            action = "discharge"
            
        total_grid_kwh += g_val
        total_cost_bdt += g_val * tariff_dict[h]
        if g_val > peak_grid_kwh:
            peak_grid_kwh = g_val
            
        hourly_plan.append({
            "hour": h,
            "grid_kwh": round(g_val, 4),
            "solar_used_kwh": round(s_val, 4),
            "battery_action": action,
            "battery_kwh": round(c_val if action == "charge" else d_val, 4),
            "battery_energy_after_kwh": round(e_val, 4)
        })
        
    summary = {
        "scenario_id": scenario_id,
        "total_cost_bdt": round(total_cost_bdt, 2),
        "total_grid_kwh": round(total_grid_kwh, 4),
        "peak_grid_kwh": round(peak_grid_kwh, 4)
    }
    
    return hourly_plan, total_grid_kwh, total_cost_bdt, peak_grid_kwh, summary
