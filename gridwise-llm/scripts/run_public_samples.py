import argparse
import json
import os
import httpx

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(script_dir, "..", "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")
    
    with open(data_path, "r", encoding="utf-8") as f:
        cases_data = json.load(f)
        
    cases = cases_data["cases"]
    results = []

    for idx, case in enumerate(cases):
        req_data = case["input"]
        scenario_id = req_data["scenario_id"]
        
        print(f"[{idx+1}/{len(cases)}] Processing {scenario_id}...")
        
        try:
            resp = httpx.post(f"{args.url}/optimize-energy", json=req_data, timeout=30.0)
            if resp.status_code != 200:
                results.append((scenario_id, "FAIL", f"HTTP {resp.status_code}: {resp.text}"))
                continue
                
            resp_data = resp.json()
            
            passed = True
            reasons = []
            
            expected_dirs = case["expected_output"]["directive_interpretation"]
            actual_dirs = resp_data.get("directive_interpretation", [])
            
            if len(expected_dirs) != len(actual_dirs):
                passed = False
                reasons.append("Directive count mismatch")
            else:
                for ed, ad in zip(expected_dirs, actual_dirs):
                    if ad.get("directive_type") != ed["directive_type"]:
                        passed = False
                        reasons.append("Directive type mismatch")
                    adj = ad.get("structured_adjustment") or {}
                    ed_adj = ed.get("structured_adjustment") or {}
                    if set(adj.get("hours", [])) != set(ed_adj.get("hours", [])):
                        passed = False
                        reasons.append("Directive hours mismatch")
                    for k, v in ed_adj.items():
                        if k not in ["directive_type", "hours"]:
                            if abs(adj.get(k, 0) - v) > 1e-4:
                                passed = False
                                reasons.append(f"Directive {k} mismatch")
            
            hourly_plan = resp_data["hourly_plan"]
            battery = req_data["battery"]
            recalc_cost = 0.0
            
            for i, h in enumerate(hourly_plan):
                demand = req_data["hours"][i]["demand_kwh"]
                supply = h["grid_kwh"] + h["solar_used_kwh"] + (h["battery_kwh"] if h["battery_action"] == "discharge" else 0.0)
                used = demand + (h["battery_kwh"] if h["battery_action"] == "charge" else 0.0)
                
                if abs(supply - used) > 1e-3:
                    passed = False
                    reasons.append(f"Energy balance failed at hour {i}")
                    
                if not (0 <= h["battery_energy_after_kwh"] <= battery["capacity_kwh"] + 1e-3):
                    passed = False
                    reasons.append(f"Capacity bounds failed at hour {i}")
                    
                tariff = req_data["hours"][i]["tariff_bdt_per_kwh"]
                recalc_cost += h["grid_kwh"] * tariff
                
            if abs(hourly_plan[23]["battery_energy_after_kwh"] - battery["initial_energy_kwh"]) > 1e-3:
                passed = False
                reasons.append("End neutrality failed")
                
            if abs(recalc_cost - resp_data["total_cost_bdt"]) > 0.01:
                passed = False
                reasons.append("Cost mismatch")
                
            if passed:
                results.append((scenario_id, "PASS", ""))
            else:
                results.append((scenario_id, "FAIL", ", ".join(reasons)))
                
        except Exception as e:
            results.append((scenario_id, "FAIL", str(e)))
            
    print(f"{'Scenario ID':<15} | {'Result':<6} | {'Reason'}")
    print("-" * 50)
    for sid, res, reason in results:
        print(f"{sid:<15} | {res:<6} | {reason}")

if __name__ == "__main__":
    main()
