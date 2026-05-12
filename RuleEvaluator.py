import argparse
import json
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from enum import Enum
import os

class Color(Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    OFF = "OFF"

@dataclass
class Probe:
    name: str
    value: float

@dataclass
class RuleResult:
    rule_name: str
    probe_name: str
    color: Color
    result_value: float

class RuleEvaluator:
    def __init__(self, rules_config: Dict):
        self.probes = {}
        self.rules = []
        for r in rules_config.get('rules', []):
            self.rules.append(Rule.from_dict(r, self.probes))

    @classmethod
    def from_file(cls, filepath: str):
        with open(filepath, 'r') as f:
            config = json.load(f)
        return cls(config)

    def evaluate_rules(self, new_probes: Dict[str, float], prev_probes: Optional[Dict[str, float]] = None) -> List[RuleResult]:

        self.probes.update(new_probes)
        results = []
        for rule in self.rules:
            result_value = rule.compute(self.probes, prev_probes)
            
            color = rule.get_color(result_value.result_value)
            results.append(RuleResult(rule.name, result_value.probe_name, color, result_value.result_value))
        return results

class Rule:
    def __init__(self, name: str, algorithm: str, probe_names: List[str], colors: List[Dict]):
        self.name = name
        self.algorithm = algorithm
        self.probe_names: List[str] = probe_names
        self.ranges = []
        for c in colors:
            color = Color[c['color'].upper()]
            self.ranges.append((color, c['min'], c['max']))

    @classmethod
    def from_dict(cls, data: Dict, default_probes: Dict[str, float]):
        name = data['name']
        algorithm = data['algorithm']
        probe_names = data['probeNames']
        colors = data['colors']
        return cls(name, algorithm, probe_names, colors)

    def compute(self, probes: Dict[str, float], prev_probes: Optional[Dict[str, float]]) -> RuleResult:
        values = []
        val = RuleResult("", "", Color.OFF, 0)
        for pn in self.probe_names:
            if pn in probes:
                val = RuleResult("", pn, Color.OFF, probes.get(pn, 0.0))
                values.append(val)

        if self.algorithm == 'average':
            result = RuleResult("", "", Color.OFF, 0)
            probe_sum = 0
            for v in values:
                probe_sum=probe_sum+v.result_value
            if len(values)>0:
                result.result_value = probe_sum/len(values)
            return result
        
        elif self.algorithm == 'max':
            result = RuleResult("", "", Color.OFF, -9999999)
            for v in values:
                if result.result_value < v.result_value:
                    result = v
            return result

        elif self.algorithm == 'min':
            result = RuleResult("", "", Color.OFF, 9999999)
            for v in values:
                if result.result_value > v.result_value:
                    result = v
            return result
        elif self.algorithm == 'averageDiff':
            result = RuleResult("", "", Color.OFF, 0)
            probe_sum = 0
            for v in values:
                probe_sum=probe_sum+v.result_value
            if len(values)>0:
                probe_avg = probe_sum/len(values)
                probe_diff = 0
                for v in values:
                    current_diff = abs(v.result_value-probe_avg)
                    if(current_diff>probe_diff):
                        probe_diff = current_diff
                        result = v
                        result.result_value = current_diff
            return result

        else:
            raise ValueError(f"Unknown algorithm: {self.algorithm}")

    def get_color(self, value: float) -> Color:
        for color, min_val, max_val in self.ranges:
            if min_val <= value <= max_val:
                return color
        return Color.OFF

def exampleEvaluation():
    parser = argparse.ArgumentParser(description='Evaluate rules based on probe values')
    parser.add_argument('--rules-file', required=True, help='Path to rules JSON file')
    parser.add_argument('--new-probes', required=True, help='JSON string of new probe values, e.g. {"e1":660,"e2":680}')
    parser.add_argument('--prev-probes', help='JSON string of previous probe values (optional)')
    args = parser.parse_args()

    try:
        evaluator = RuleEvaluator.from_file(args.rules_file)
        new_probes = json.loads(args.new_probes)
        prev_probes = json.loads(args.prev_probes) if args.prev_probes else None

        results = evaluator.evaluate_rules(new_probes, prev_probes)
        for r in results:
            print(f"Rule '{r.rule_name}': {r.color.value} (value: {r.result_value:.2f})")
    except Exception as e:
        print(f"Error: {e}")


