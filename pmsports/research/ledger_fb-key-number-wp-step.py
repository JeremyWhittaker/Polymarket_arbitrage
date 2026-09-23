"""Export only the repaired, version-checked causal football ledger; no resimulation."""
import importlib.util
from pathlib import Path
SLUG = 'fb-key-number-wp-step'
spec = importlib.util.spec_from_file_location('football_pairs', Path(__file__).with_name('h_fb-ladder-monotonicity-arb.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

def build():
    return H.export(SLUG)

if __name__ == '__main__':
    doc = build()
    print(f"{SLUG}: {doc['n_total_trades']} complete audited signals")
