"""Local CLI runner — a drop-in alternative to the HTTP Function for testing.

Example:
  python run_local.py --input /path/CB_MM_LTP_AUGUST.xlsx --customer default --out ./_out
"""
import argparse
import json
from agent import run_agent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to the customer .xlsx")
    ap.add_argument("--customer", default="default", help="customer_id (config file stem)")
    ap.add_argument("--out", default=None, help="Local output dir")
    args = ap.parse_args()

    resp = run_agent({
        "customer_id": args.customer,
        "input": {"path": args.input},
        "output_dir": args.out,
    })
    # Print the report (not the whole CSVs) for readability
    printable = {k: v for k, v in resp.items() if k != "outputs_inline"}
    print(json.dumps(printable, indent=2, default=str))


if __name__ == "__main__":
    main()
