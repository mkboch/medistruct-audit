import argparse, json, csv
from pathlib import Path
from utils import jaccard

def get_list(d, k):
    v = d.get(k, [])
    if not isinstance(v, list): return []
    return [str(x).strip().lower() for x in v if str(x).strip()]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--s_dir", required=True, help="normalized S")
    ap.add_argument("--sp_dir", required=True, help="normalized Sprime")
    ap.add_argument("--out_csv", required=True)
    args = ap.parse_args()

    s_dir = Path(args.s_dir)
    sp_dir = Path(args.sp_dir)
    rows = []

    for p in sorted(s_dir.glob("*.json")):
        note_id = p.stem
        sp = sp_dir / f"{note_id}.json"
        if not sp.exists():
            continue
        S = json.loads(p.read_text())
        Sp = json.loads(sp.read_text())

        row = {"note_id": note_id}
        for f in ["symptoms","diagnoses","medications"]:
            row[f"{f}_jaccard"] = jaccard(get_list(S,f), get_list(Sp,f))
        rows.append(row)

    out = Path(args.out_csv)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    print("Wrote", out, "rows:", len(rows))

if __name__ == "__main__":
    main()