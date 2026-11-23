import argparse
import json
from pathlib import Path
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    usecols = ["note_id", "subject_id", "hadm_id", "note_type", "text"]
    rows_written = 0

    with out_path.open("w", encoding="utf-8") as f:
        for chunk in pd.read_csv(in_path, compression="infer", usecols=usecols, chunksize=1000):
            for _, r in chunk.iterrows():
                rec = {
                    "note_id": str(r["note_id"]),
                    "subject_id": int(r["subject_id"]),
                    "hadm_id": int(r["hadm_id"]) if not pd.isna(r["hadm_id"]) else None,
                    "note_type": "discharge",
                    "text": "" if pd.isna(r["text"]) else str(r["text"]),
                    "source_table": "discharge",
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                rows_written += 1
                if args.limit and rows_written >= args.limit:
                    break
            if args.limit and rows_written >= args.limit:
                break

    print(f"Wrote {rows_written} records to {out_path}")


if __name__ == "__main__":
    main()