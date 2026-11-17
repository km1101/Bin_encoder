"""
app.py - Streamlit front end for the .sbd batch processor.

Run locally:   streamlit run app.py
Azure startup: streamlit run app.py --server.port=8000 --server.address=0.0.0.0

Environment variables
  BROWSE_ROOT   Top-level server folder users may browse / process (default: /home on
                Linux, the user's home folder elsewhere). Paths outside it are rejected.
                Point it at your Azure Files mount, e.g. /mnt/data.
"""
import csv
import os
from collections import deque

import pandas as pd
import streamlit as st

import main as sbd

st.set_page_config(page_title="SBD Batch Processor", page_icon="📡", layout="wide")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _default_root():
    if os.name != "nt" and os.path.isdir("/home"):
        return "/home"
    return os.path.expanduser("~")


BROWSE_ROOT = os.path.realpath(os.environ.get("BROWSE_ROOT") or _default_root())


def is_within_root(path):
    try:
        real = os.path.realpath(path)
        return os.path.commonpath([real, BROWSE_ROOT]) == BROWSE_ROOT
    except ValueError:  # different drive on Windows
        return False


def parse_folders(text):
    seen, out = set(), []
    for line in (text or "").splitlines():
        p = line.strip().strip('"').strip("'")
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def tail_csv(path, n=20):
    with open(path, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        rows = deque(reader, maxlen=n)
    width = max([len(header)] + [len(r) for r in rows]) if rows else len(header)
    header = header + [f"col_{i}" for i in range(len(header), width)]
    data = [r + [""] * (width - len(r)) for r in rows]
    return pd.DataFrame(data, columns=header)


# --------------------------------------------------------------------------- #
# Session state (each browser session is independent)
# --------------------------------------------------------------------------- #
st.session_state.setdefault("folders_text", "")
st.session_state.setdefault("output_dir", "")
st.session_state.setdefault("browse_cur", BROWSE_ROOT)
st.session_state.setdefault("last_run", None)

# Apply a folder chosen in the browser dialog BEFORE the widgets are created
pending = st.session_state.pop("pending_pick", None)
if pending:
    if pending["target"] == "folders":
        current = parse_folders(st.session_state["folders_text"])
        if pending["path"] not in current:
            current.append(pending["path"])
        st.session_state["folders_text"] = "\n".join(current)
    else:
        st.session_state["output_dir"] = pending["path"]


# --------------------------------------------------------------------------- #
# Server-side folder browser
# --------------------------------------------------------------------------- #
@st.dialog("Browse server folders", width="large")
def browse_dialog(target):
    cur = st.session_state.get("browse_cur", BROWSE_ROOT)
    if not os.path.isdir(cur) or not is_within_root(cur):
        cur = BROWSE_ROOT
    cur = os.path.realpath(cur)
    st.session_state["browse_cur"] = cur

    st.caption(f"Browsing is limited to `{BROWSE_ROOT}` on the server.")
    st.code(cur, language=None)

    c1, c2 = st.columns(2)
    if c1.button("⬆ Up one level", disabled=(cur == BROWSE_ROOT), use_container_width=True):
        st.session_state["browse_cur"] = os.path.dirname(cur)
        st.rerun(scope="fragment")
    label = "✔ Add this folder" if target == "folders" else "✔ Use as output folder"
    if c2.button(label, type="primary", use_container_width=True):
        st.session_state["pending_pick"] = {"target": target, "path": cur}
        st.rerun()

    try:
        entries = sorted(os.listdir(cur), key=str.lower)
    except OSError as e:
        st.error(f"Cannot open folder: {e}")
        return
    subdirs = [e for e in entries if not e.startswith(".") and os.path.isdir(os.path.join(cur, e))]
    n_sbd = sum(1 for e in entries if e.lower().endswith(".sbd"))
    st.caption(f"{len(subdirs)} sub-folder(s), {n_sbd} .sbd file(s) here")

    for i, d in enumerate(subdirs[:200]):
        if st.button(f"📁 {d}", key=f"dir_{i}", use_container_width=True):
            st.session_state["browse_cur"] = os.path.join(cur, d)
            st.rerun(scope="fragment")
    if len(subdirs) > 200:
        st.info("Only the first 200 sub-folders are shown.")


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("⚙️ Configuration")

    st.text_area(
        "Input folders (one per line)",
        key="folders_text",
        height=170,
        placeholder="/home/data/Britt2\n/home/data/Celtic2",
        help="Server-side folder paths containing .sbd files.",
    )
    if st.button("📂 Browse & add folder", use_container_width=True):
        browse_dialog("folders")

    st.text_input(
        "Output folder (optional)",
        key="output_dir",
        help="Shared folder for eng_data.csv, weather_data.csv and wave_data.csv. "
             "Leave blank to write the CSVs into each source folder.",
    )
    if st.button("📂 Browse output folder", use_container_width=True):
        browse_dialog("output")

    with st.expander("Advanced"):
        format_dir = st.text_input("Format files folder (bf-*.csv)", value=sbd.DEFAULT_FORMAT_DIR)
        parameters_file = st.text_input("Wave header (parameters.csv)", value=sbd.DEFAULT_PARAMETERS_FILE)
        eng_header_file = st.text_input("ENG header CSV", value=sbd.DEFAULT_ENG_HEADER_FILE)
        weather_header_file = st.text_input("Weather header CSV", value=sbd.DEFAULT_WEATHER_HEADER_FILE)
        workers = st.slider("Parallel workers", 1, 16, min(8, os.cpu_count() or 2))

    run_clicked = st.button("▶ Run Processing", type="primary", use_container_width=True)


# --------------------------------------------------------------------------- #
# Main page
# --------------------------------------------------------------------------- #
st.title("📡 SBD Batch Processor")
st.caption("Decodes Iridium .sbd files into eng_data.csv, weather_data.csv and wave_data.csv, "
           "then archives the processed files to `Obs_archive`.")

if run_clicked:
    folders = parse_folders(st.session_state["folders_text"])
    out_dir = (st.session_state["output_dir"] or "").strip()

    problems = []
    if not folders:
        problems.append("Add at least one input folder.")
    for p in folders:
        if not is_within_root(p):
            problems.append(f"Not allowed (outside `{BROWSE_ROOT}`): {p}")
        elif not os.path.isdir(p):
            problems.append(f"Folder not found on the server: {p}")
    if out_dir and not is_within_root(out_dir):
        problems.append(f"Output folder not allowed (outside `{BROWSE_ROOT}`): {out_dir}")
    for label, path in [("Format folder", format_dir), ("Wave header", parameters_file),
                        ("ENG header", eng_header_file), ("Weather header", weather_header_file)]:
        if not os.path.exists(path):
            problems.append(f"{label} not found: {path}")

    if problems:
        for msg in problems:
            st.error(msg)
    else:
        log_lines = []
        with st.expander("📜 Processing log", expanded=True):
            box = st.empty()

        def on_log(line):
            log_lines.append(line)
            box.code("\n".join(log_lines[-400:]), language=None)

        with st.spinner("Processing..."):
            try:
                summary = sbd.process_folders_batch(
                    folders,
                    output_dir=out_dir or None,
                    format_dir=format_dir,
                    parameters_file=parameters_file,
                    eng_header_file=eng_header_file,
                    weather_header_file=weather_header_file,
                    max_workers=workers,
                    log_callback=on_log,
                )
                st.session_state["last_run"] = {"summary": summary, "log": log_lines}
            except Exception as e:
                on_log(f"FATAL: {e}")
                st.error(f"Processing failed: {e}")

last = st.session_state.get("last_run")
if last:
    s = last["summary"]
    if not run_clicked:
        with st.expander("📜 Processing log (last run)", expanded=False):
            st.code("\n".join(last["log"][-400:]), language=None)

    st.subheader("Results")
    m = st.columns(6)
    m[0].metric("Files found", s["files_found"])
    m[1].metric("ENG", s["counts"]["eng"])
    m[2].metric("Weather", s["counts"]["weather"])
    m[3].metric("Wave", s["counts"]["wave"])
    m[4].metric("Locked", s["locked"])
    m[5].metric("Errors", s["errors"])
    if s["folders_skipped"]:
        st.warning("Skipped folders: " + ", ".join(s["folders_skipped"]))

    outputs = [p for p in s["outputs"] if os.path.isfile(p)]
    if not outputs:
        st.info("No CSV files were written in the last run.")
    else:
        st.subheader("Download CSV files")
        for i, path in enumerate(outputs):
            folder_label = os.path.basename(os.path.dirname(path)) or path
            name = os.path.basename(path)
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{name}** &nbsp; `{os.path.dirname(path)}`")
            with open(path, "rb") as fh:
                c2.download_button(
                    f"⬇ {folder_label}/{name}", fh.read(), file_name=name,
                    mime="text/csv", key=f"dl_{i}", use_container_width=True,
                )
            with st.expander(f"Preview last 20 rows - {folder_label}/{name}"):
                try:
                    st.dataframe(tail_csv(path), use_container_width=True)
                except Exception as e:
                    st.warning(f"Preview unavailable: {e}")
elif not run_clicked:
    st.info("Add one or more input folders in the sidebar and click **Run Processing**.")
