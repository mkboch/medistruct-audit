# Data access

## What is not in this repository
This repository distributes **source code only**. It contains none of the following:
- MIMIC-IV-Note notes, tables or files, nor any data derived from them at the patient or note level, such as extraction views, model outputs, receipts with note identifiers, evidence spans or regenerated notes;
- the 50-note reference labels or per-note predictions;
- data from the external pilot used as one held-out audit case;
- per-run agent traces.

## MIMIC-IV-Note
MIMIC-IV-Note is a restricted-access resource. Obtain it yourself from PhysioNet (https://physionet.org/content/mimic-iv-note/), which requires:
- credentialed access;
- completion of the required human-subjects training;
- signing the PhysioNet Credentialed Health Data Use Agreement 1.5.0.

Do not ask the authors for copies; they are not permitted to share the data.

## Where to put your own authorised copy
Keep data outside version control. The provided `.gitignore` excludes `data_local/`, `Dataset/`, `Outputs/`, `*.csv.gz`, `*.jsonl` and run directories. A suggested layout is:

```
data_local/
  discharge.csv.gz            # your own copy of MIMIC-IV-Note discharge.csv.gz
  inputs_5k.jsonl             # produced by mimic_discharge_loader.py
  extract_run/                # produced by run_extract.py
```

To audit the historical layout with the Part B tools, set `MEDSTRUCT_HIST_DIR` to a directory containing `Codes/`, `Dataset/` and `Outputs/` in the original structure.

## Language models and online services
Run every model that receives clinical text **locally**, on infrastructure you control. PhysioNet's guidance on large language models states that credentialed data must not be sent to third-party online services unless:
- zero data retention is verified;
- data are not used for training; and
- there is no human review.

The extraction scripts send text to whatever `OPENAI_BASE_URL` you configure, so set it to a local server only.

In the agentic audit, the language model never receives clinical text. Audit tools return counts, rates, identifiers, hashes and code lines only, and the tool server runs in a Landlock sandbox. If you audit restricted runs, tool results still contain record identifiers, so keep run directories private.
