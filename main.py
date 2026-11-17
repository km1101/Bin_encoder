"""
main.py - batch decoder for Iridium .sbd AWS/buoy files.

No hardcoded paths: every function takes its folders / output dir / format dir /
parameter files as arguments.

Design notes
------------
* Worker processes only DECODE (pure, no file writes, no os.chdir).
* The calling process writes the CSVs and archives the files, so many workers
  can never corrupt a shared CSV by appending at the same time.
* Original size rules are kept:
      < 50 bytes     -> ENG data      (comma separated text)
      50 - <100      -> Weather data  (comma separated text)
      100 - 900      -> Wave data     (binary, decoded with bf-*.csv formats)
      > 900          -> skipped (and archived, as in the original script)
"""
import argparse
import contextlib
import csv
import multiprocessing as mp
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

from binstring import bintofield, datatobin, prefixzeros

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FORMAT_DIR = APP_DIR  # folder holding the bf-*.csv files
DEFAULT_PARAMETERS_FILE = os.path.join(APP_DIR, "parameters.csv")  # wave header
DEFAULT_ENG_HEADER_FILE = os.path.join(APP_DIR, "eng_header.csv")
DEFAULT_WEATHER_HEADER_FILE = os.path.join(APP_DIR, "weather_header.csv")

OUTPUT_NAMES = {
    "eng": "eng_data.csv",
    "weather": "weather_data.csv",
    "wave": "wave_data.csv",
}
ARCHIVE_DIRNAME = "Obs_archive"
LOCK_NAME = ".sbd_processing.lock"
LOCK_STALE_SECONDS = 2 * 60 * 60


# --------------------------------------------------------------------------- #
# Logging helper
# --------------------------------------------------------------------------- #
def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _make_logger(log_callback):
    def log(msg):
        line = f"[{_ts()}] {msg}"
        if log_callback:
            log_callback(line)
        else:
            print(line, flush=True)
    return log


# --------------------------------------------------------------------------- #
# Header files (one column name per line, same layout as parameters.csv)
# --------------------------------------------------------------------------- #
def read_header_file(path):
    """Return the non-empty lines of a header file as a list of column names."""
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"Header file not found: {path}")
    header = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            cell = line.strip()
            if cell:
                header.append(cell)
    return header


def read_wave_header(parameters_file=DEFAULT_PARAMETERS_FILE):
    """Wave header columns (parameters.csv)."""
    return read_header_file(parameters_file)


def load_headers(
    parameters_file=DEFAULT_PARAMETERS_FILE,
    eng_header_file=DEFAULT_ENG_HEADER_FILE,
    weather_header_file=DEFAULT_WEATHER_HEADER_FILE,
):
    return {
        "eng": read_header_file(eng_header_file),
        "weather": read_header_file(weather_header_file),
        "wave": read_header_file(parameters_file),
    }


def ensure_csv_header(filepath, header):
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        with open(filepath, "w", newline="") as file:
            csv.writer(file).writerow(header)


def write_output_to_csv(output_list, filename):
    with open(filename, "a", newline="") as file:
        csv.writer(file).writerow(output_list)


def _append_rows(filepath, header, rows):
    ensure_csv_header(filepath, header)
    with open(filepath, "a", newline="") as file:
        csv.writer(file).writerows(rows)


# --------------------------------------------------------------------------- #
# Binary decoding (logic unchanged, but no os.chdir and no global paths)
# --------------------------------------------------------------------------- #
def decodebin(inputfile, format_dir=DEFAULT_FORMAT_DIR):
    with open(inputfile, "rb") as workfile:
        message = workfile.read()

    BinaryString = "".join(bintofield(datatobin(byte), 8) for byte in message)
    datastring = BinaryString[0:8]
    dataint = int(datastring, 2)

    if (dataint < 49 or dataint > 51) and (dataint < 67 or dataint > 69) and (dataint != 36):
        formatfile = os.path.join(
            format_dir, f"bf-{prefixzeros(dataint)}{dataint}.csv"
        )
        with open(formatfile, "r") as workfile:
            fieldnames = workfile.readline().strip()
            multipliers = workfile.readline().strip()
            offsets = workfile.readline().strip()
            dataformat = workfile.readline().strip()
            numberofbits = workfile.readline().strip()
            maxvalue = workfile.readline().strip()  # noqa: F841 (kept from original)

        FormatList = dataformat.split(",")
        MultiList = multipliers.split(",")
        OffsetList = offsets.split(",")
        BitsList = numberofbits.split(",")

        OutputList = []
        c = d = 0
        datapresent = 1
        numberoffields = 1
        fieldcount = 1

        while c < len(BinaryString):
            if d < len(FormatList):
                fieldformat = int(FormatList[d])
                bits = int(BitsList[d])
                if fieldformat == 0 and datapresent:
                    datastring = BinaryString[c:c + bits]
                    datafloat = (int(datastring, 2) + float(OffsetList[d])) / float(MultiList[d])
                    OutputList.append(datafloat)
                    c += bits
                elif fieldformat == 1:
                    datapresent = int(BinaryString[c])
                    OutputList.append(float(datapresent))
                    c += 1
                elif fieldformat == 2 and datapresent:
                    if fieldcount < numberoffields:
                        datastring = BinaryString[c:c + bits]
                        datafloat = (int(datastring, 2) + float(OffsetList[d])) / float(MultiList[d])
                        OutputList.append(datafloat)
                        c += bits
                        fieldcount += 1
                    else:
                        OutputList.append(0.0)
                elif fieldformat >= 3 and datapresent:
                    datastring = BinaryString[c:c + bits]
                    numberoffields = int(datastring, 2) * (fieldformat - 2)
                    fieldcount = 0
                    OutputList.append(float(numberoffields / (fieldformat - 2)))
                    c += bits
                else:
                    OutputList.append(0.0)
                d += 1
            else:
                break
    else:
        OutputList = [x.decode("utf-8") for x in message.split(b",")]

    return OutputList


# --------------------------------------------------------------------------- #
# Per-file work
# --------------------------------------------------------------------------- #
def classify_size(file_size):
    if file_size < 50:
        return "eng"
    if file_size < 100:
        return "weather"
    if file_size <= 900:
        return "wave"
    return "skipped"


def decode_file(inputfile, format_dir=DEFAULT_FORMAT_DIR):
    """
    Pure decode step (safe in worker processes, writes nothing).
    Returns {"file", "kind", "rows", "error", "archive"}.
    """
    result = {"file": inputfile, "kind": None, "rows": [], "error": None, "archive": True}
    try:
        kind = classify_size(os.path.getsize(inputfile))
        result["kind"] = kind
        if kind in ("eng", "weather"):
            try:
                with open(inputfile, "r") as f:
                    content = f.read()
                result["rows"] = [line.split(",") for line in content.strip().splitlines()]
            except Exception as e:
                result["error"] = f"Read error: {e}"
                result["archive"] = False
        elif kind == "wave":
            try:
                result["rows"] = [decodebin(inputfile, format_dir)]
            except Exception as e:
                # original behaviour: log the decode error and still archive
                result["error"] = f"Wave decode error: {e}"
    except Exception as e:
        result["error"] = f"Error processing: {e}"
        result["archive"] = False
    return result


def _unique_dest(archive_folder, name):
    dest = os.path.join(archive_folder, name)
    if not os.path.exists(dest):
        return dest
    stem, ext = os.path.splitext(name)
    return os.path.join(archive_folder, f"{stem}_{int(time.time() * 1000)}{ext}")


def finalize_result(result, output_dir, headers, log):
    """
    Write decoded rows to CSV and archive the source file.
    output_dir=None -> write next to the source file.
    Returns (status, written_csv_path_or_None).
    """
    inputfile = result["file"]
    name = os.path.basename(inputfile)
    src_dir = os.path.dirname(inputfile)
    out_dir = output_dir or src_dir
    kind = result["kind"]
    should_archive = result["archive"]
    status, written = "ok", None

    if result["error"]:
        log(f"{result['error']} ({name})")
        status = "error"

    if kind in OUTPUT_NAMES and result["rows"] and not result["error"]:
        try:
            os.makedirs(out_dir, exist_ok=True)
            target = os.path.join(out_dir, OUTPUT_NAMES[kind])
            _append_rows(target, headers[kind], result["rows"])
            written = target
            log(f"Wrote {name} -> {OUTPUT_NAMES[kind]}")
        except PermissionError:
            log(f"CSV locked (Excel open?). Skipping {name}.")
            should_archive, status = False, "locked"
        except Exception as e:
            log(f"Write error for {name}: {e}")
            should_archive, status = False, "error"
    elif kind == "skipped":
        log(f"Skipped (too large): {name}")
        status = "skipped"

    if should_archive:
        try:
            archive_folder = os.path.join(src_dir, ARCHIVE_DIRNAME)
            os.makedirs(archive_folder, exist_ok=True)
            shutil.move(inputfile, _unique_dest(archive_folder, name))
            log(f"Moved {name} to {ARCHIVE_DIRNAME}")
        except Exception as e:
            log(f"Archiving error ({name}): {e}")
            status = "error"
    return status, written


def process_single_file(
    inputfile,
    output_dir=None,
    format_dir=DEFAULT_FORMAT_DIR,
    parameters_file=DEFAULT_PARAMETERS_FILE,
    eng_header_file=DEFAULT_ENG_HEADER_FILE,
    weather_header_file=DEFAULT_WEATHER_HEADER_FILE,
    log_callback=None,
):
    """Process one .sbd file end to end (decode, write CSV, archive)."""
    log = _make_logger(log_callback)
    headers = load_headers(parameters_file, eng_header_file, weather_header_file)
    result = decode_file(inputfile, format_dir)
    return finalize_result(result, output_dir, headers, log)


# --------------------------------------------------------------------------- #
# Folder locking (several web users may point at the same folder)
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def _folder_lock(folder):
    lock_path = os.path.join(folder, LOCK_NAME)
    acquired = False
    try:
        if os.path.exists(lock_path) and time.time() - os.path.getmtime(lock_path) > LOCK_STALE_SECONDS:
            with contextlib.suppress(OSError):
                os.remove(lock_path)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            acquired = True
        except FileExistsError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                os.remove(lock_path)


# --------------------------------------------------------------------------- #
# Batch
# --------------------------------------------------------------------------- #
def process_folders_batch(
    folder_paths,
    output_dir=None,
    format_dir=DEFAULT_FORMAT_DIR,
    parameters_file=DEFAULT_PARAMETERS_FILE,
    eng_header_file=DEFAULT_ENG_HEADER_FILE,
    weather_header_file=DEFAULT_WEATHER_HEADER_FILE,
    max_workers=None,
    log_callback=None,
):
    """
    Process every .sbd file in each folder (non-recursive, as in the original).

    output_dir : shared folder for eng/weather/wave CSVs. If empty/None, each
                 source folder gets its own CSVs.
    Returns a summary dict: files_found, counts, errors, outputs (list of CSV paths).
    """
    log = _make_logger(log_callback)
    summary = {
        "files_found": 0,
        "counts": {"eng": 0, "weather": 0, "wave": 0, "skipped": 0},
        "locked": 0,
        "errors": 0,
        "outputs": [],
        "folders_skipped": [],
    }
    outputs = []

    headers = load_headers(parameters_file, eng_header_file, weather_header_file)
    output_dir = output_dir.strip() if output_dir else None
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    workers = max_workers or min(8, os.cpu_count() or 2)

    with contextlib.ExitStack() as stack:
        all_files = []
        for folder in folder_paths:
            if not os.path.isdir(folder):
                log(f"Folder not found: {folder}")
                summary["folders_skipped"].append(folder)
                continue
            if not stack.enter_context(_folder_lock(folder)):
                log(f"Folder is being processed by another session, skipped: {folder}")
                summary["folders_skipped"].append(folder)
                continue
            files = sorted(
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if f.lower().endswith(".sbd") and os.path.isfile(os.path.join(folder, f))
            )
            log(f"{folder}: {len(files)} .sbd file(s)")
            all_files.extend(files)

        summary["files_found"] = len(all_files)
        if not all_files:
            log("No new files found.")
            return summary

        log(f"Processing {len(all_files)} file(s) with {workers} worker(s)...")
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as executor:
            futures = [executor.submit(decode_file, f, format_dir) for f in all_files]
            # consume in submission order so CSV rows stay in file-name order
            for f, fut in zip(all_files, futures):
                try:
                    result = fut.result()
                except Exception as e:
                    result = {"file": f, "kind": None, "rows": [], "error": f"Worker failure: {e}", "archive": False}
                status, written = finalize_result(result, output_dir, headers, log)
                if result["kind"] in summary["counts"] and status == "ok":
                    summary["counts"][result["kind"]] += 1
                if status == "locked":
                    summary["locked"] += 1
                elif status == "error":
                    summary["errors"] += 1
                if written and written not in outputs:
                    outputs.append(written)

    summary["outputs"] = outputs
    log("Batch complete.")
    return summary


# --------------------------------------------------------------------------- #
# Command line (optional): python main.py FOLDER [FOLDER ...] [--output DIR]
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Decode .sbd files in one or more folders.")
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--output", default=None)
    ap.add_argument("--format-dir", default=DEFAULT_FORMAT_DIR)
    ap.add_argument("--parameters", default=DEFAULT_PARAMETERS_FILE)
    ap.add_argument("--eng-header", default=DEFAULT_ENG_HEADER_FILE)
    ap.add_argument("--weather-header", default=DEFAULT_WEATHER_HEADER_FILE)
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    process_folders_batch(
        a.folders, a.output, a.format_dir, a.parameters,
        a.eng_header, a.weather_header, a.workers,
    )
