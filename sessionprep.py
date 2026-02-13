import os
import sys
import argparse

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
    from rich import box
except ImportError:
    print("Error: The 'rich' library is required for the CLI but not installed.", file=sys.stderr)
    print("Please install it with: pip install sessionprep[cli] (or uv sync --extra cli)", file=sys.stderr)
    sys.exit(1)

from sessionpreplib import __version__
from sessionpreplib.pipeline import Pipeline, load_session
from sessionpreplib.detectors import default_detectors
from sessionpreplib.processors import default_processors
from sessionpreplib.config import default_config, merge_configs
from sessionpreplib.rendering import build_diagnostic_summary, render_diagnostic_summary_text
from sessionpreplib.reports import generate_report, save_json, build_warnings
from sessionpreplib.utils import protools_sort_key
from sessionpreplib.audio import AUDIO_EXTENSIONS
from sessionpreplib.events import EventBus

console = Console()


def positive_int(value):
    ivalue = int(value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return ivalue


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="SessionPrep",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--version", action="version",
                        version=f"sessionprep {__version__}")
    
    parser.add_argument("directory", type=str, 
                        help="Source directory containing audio tracks (.wav, .aif, .aiff)")
    
    # Targets
    parser.add_argument("--target_rms", type=float, default=-18.0, 
                        help="Target RMS for sustained sources (dBFS)")
    parser.add_argument("--target_peak", type=float, default=-6.0, 
                        help="Target/max peak level (dBFS)")
    parser.add_argument("--crest_threshold", type=float, default=12.0, 
                        help="Crest factor threshold for transient detection (dB)")
    
    # Diagnostics & Detection
    parser.add_argument("--clip_consecutive", type=int, default=3,
                        help="Number of consecutive samples at ±1.0 to flag as clipped")
    parser.add_argument("--clip_report_max_ranges", type=positive_int, default=10,
                        help="Max clipped sample ranges to include in reports per file")
    parser.add_argument("--dc_offset_warn_db", type=float, default=-40.0,
                        help="Warn if DC offset exceeds this level (dBFS)")
    parser.add_argument("--corr_warn", type=float, default=-0.3,
                        help="Warn if stereo L/R correlation is below this value")
    parser.add_argument("--dual_mono_eps", type=float, default=1e-5,
                        help="Warn if stereo file appears dual-mono (max |L-R| <= eps)")
    parser.add_argument("--mono_loss_warn_db", type=float, default=6.0,
                        help="Warn if stereo file loses more than this many dB when folded to mono")

    parser.add_argument("--one_sided_silence_db", type=float, default=-80.0,
                        help="Warn if a stereo file has one channel at or below this RMS level (dBFS) while the other is not")

    parser.add_argument("--subsonic_hz", type=float, default=30.0,
                        help="Subsonic detector cutoff frequency (Hz)")
    parser.add_argument("--subsonic_warn_ratio_db", type=float, default=-20.0,
                        help="Warn if subsonic power ratio (<= cutoff) exceeds this level (dB relative to full-band power)")
    
    # Analysis
    parser.add_argument("--window", type=positive_int, default=400, 
                        help="RMS analysis window (ms)")
    parser.add_argument("--stereo_mode", type=str, choices=["avg", "sum"], 
                        default="avg", help="Stereo RMS calculation mode")

    # RMS Anchor (momentary window statistics)
    parser.add_argument("--rms_anchor", type=str, choices=["percentile", "max"],
                        default="percentile",
                        help="Momentary RMS anchor strategy used for gain calculations")
    parser.add_argument("--rms_percentile", type=float, default=95.0,
                        help="Percentile used when --rms_anchor percentile (0-100)")
    parser.add_argument("--gate_relative_db", type=float, default=40.0,
                        help="Relative gate for momentary RMS windows. Ignore windows more than this many dB below the loudest RMS window (e.g. 40 means keep windows within 40 dB of the loudest).")
    parser.add_argument("--tail_max_regions", type=positive_int, default=20,
                        help="Max number of upper-tail regions to include in the report per file")
    parser.add_argument("--tail_min_exceed_db", type=float, default=3.0,
                        help="Only report tail regions exceeding the anchor by at least this many dB")
    parser.add_argument("--tail_hop_ms", type=positive_int, default=10,
                        help="Hop size for tail region reporting (ms). Larger values reduce the number of reported regions.")
    
    # Balance restoration
    parser.add_argument("--anchor", type=str, default=None,
                        help="Anchor track filename (fader stays at 0dB)")
    parser.add_argument("--normalize_faders", action="store_true",
                        help="Shift all fader offsets so loudest track = 0dB")

    parser.add_argument("--force_transient", nargs='+', default=[], action='extend',
        help="Keywords to force TRANSIENT mode. Supports: substring ('kick'), "
             "glob patterns ('Kick*.wav'), or exact match ('Kick_01.wav$')")
    parser.add_argument("--force_sustained", nargs='+', default=[], action='extend',
        help="Keywords to force SUSTAINED mode. Supports: substring ('pad'), "
             "glob patterns ('Synth_*.wav'), or exact match ('Bass_01.wav$')")

    parser.add_argument("--group", action="append", default=[],
                        help="Named gain-linked group. Syntax: Name:pattern1,pattern2  "
                             "(e.g. --group Kick:kick,kick_sub). Patterns support "
                             "substring, glob (*/?), or exact match (suffix $). "
                             "First match wins; overlaps produce a warning.")

    # Output Configuration
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite source WAV files in-place (creates backups)")
    parser.add_argument("-x", "--execute", action="store_true",
                        help="Execute processing (write processed WAVs and reports). Without -x this runs in analysis-only mode.")
    parser.add_argument("--output_folder", type=str, default="processed",
                        help="Subfolder name for processed files")
    parser.add_argument("--backup", type=str, default="_originals", 
                        help="Backup folder name (Only used if overwriting files)")
    
    # Reporting
    parser.add_argument("--report", type=str, default="sessionprep.txt",
                        help="Output report filename")
    parser.add_argument("--json", type=str, default="sessionprep.json",
                        help="Output JSON filename for automation")
    
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
    
    args = parser.parse_args()

    if not (0.0 < args.rms_percentile < 100.0):
        parser.error("--rms_percentile must be between 0 and 100 (exclusive)")

    if args.gate_relative_db < 0.0:
        parser.error("--gate_relative_db must be >= 0")

    if args.subsonic_hz <= 0.0:
        parser.error("--subsonic_hz must be > 0")

    if args.one_sided_silence_db > 0.0:
        parser.error("--one_sided_silence_db must be <= 0")

    return args


# ---------------------------------------------------------------------------
# Rich console rendering (CLI-only, not in the library)
# ---------------------------------------------------------------------------

def print_diagnostic_summary(summary):
    problems = summary.get("problems") or []
    attention = summary.get("attention") or []
    information = summary.get("information") or []
    clean = summary.get("clean") or []
    clean_count = int(summary.get("clean_count", 0) or 0)
    total_ok = int(summary.get("total_ok", 0) or 0)

    def item_count(groups):
        return sum(len(g.get("items") or []) for g in groups)

    def print_groups(groups, color, compact=False):
        any_printed = False
        for g in groups:
            title = g.get("title")
            hint = g.get("hint")
            items = g.get("items") or []
            if not items and not g.get("standalone"):
                continue
            header = f"{title}"
            if hint:
                header = f"{header} -> {hint}"
            console.print(f"  [{color}]-[/] {header}")
            for item in items:
                console.print(f"    [dim]* {item}[/]")
            if not compact:
                console.print("")
            any_printed = True
        return any_printed

    console.print("")
    console.print(f"[bold red]\U0001f534 PROBLEMS ({item_count(problems)})[/]")
    if not print_groups(problems, "red"):
        console.print("  [green]-[/] None")

    console.print("")
    console.print(f"[bold yellow]\U0001f7e1 ATTENTION ({item_count(attention)})[/]")
    if not print_groups(attention, "yellow"):
        console.print("  [green]-[/] None")

    console.print("")
    console.print(f"[bold blue]\U0001f535 INFORMATION ({item_count(information)})[/]")
    if not print_groups(information, "blue"):
        console.print("  [green]-[/] None")

    console.print("")
    console.print("[bold green]\U0001f7e2 CLEAN[/]")
    if not print_groups(clean, "green", compact=True):
        console.print("  [green]-[/] None")





# ---------------------------------------------------------------------------
# Main process_files() — thin wrapper around the sessionpreplib pipeline
# ---------------------------------------------------------------------------

def process_files():
    args = parse_arguments()
    source_dir = args.directory

    if not os.path.isdir(source_dir):
        console.print(f"[bold red]Error:[/] Directory '{source_dir}' not found.")
        return

    # --- OUTPUT SETUP (only in execute mode) ---
    output_dir = None
    backup_dir = None
    is_overwriting = False
    source_dir_norm = os.path.normcase(os.path.abspath(source_dir))

    if args.execute:
        if args.overwrite:
            output_dir = source_dir
            is_overwriting = True
        else:
            output_dir = os.path.join(source_dir, args.output_folder)
            output_dir_norm = os.path.normcase(os.path.abspath(output_dir))
            if source_dir_norm == output_dir_norm:
                console.print("[bold red]Error:[/] Output folder resolves to the source directory. Use --overwrite to overwrite in-place, or choose a different --output_folder.")
                return
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

        backup_dir = os.path.join(source_dir, args.backup)
        if is_overwriting:
            os.makedirs(backup_dir, exist_ok=True)

    # --- BUILD CONFIG FROM CLI ARGS ---
    config = default_config()
    cli_overrides = {
        "target_rms": args.target_rms,
        "target_peak": args.target_peak,
        "crest_threshold": args.crest_threshold,
        "clip_consecutive": args.clip_consecutive,
        "clip_report_max_ranges": args.clip_report_max_ranges,
        "dc_offset_warn_db": args.dc_offset_warn_db,
        "corr_warn": args.corr_warn,
        "dual_mono_eps": args.dual_mono_eps,
        "mono_loss_warn_db": args.mono_loss_warn_db,
        "one_sided_silence_db": args.one_sided_silence_db,
        "subsonic_hz": args.subsonic_hz,
        "subsonic_warn_ratio_db": args.subsonic_warn_ratio_db,
        "window": args.window,
        "stereo_mode": args.stereo_mode,
        "rms_anchor": args.rms_anchor,
        "rms_percentile": args.rms_percentile,
        "gate_relative_db": args.gate_relative_db,
        "tail_max_regions": args.tail_max_regions,
        "tail_min_exceed_db": args.tail_min_exceed_db,
        "tail_hop_ms": args.tail_hop_ms,
        "force_transient": args.force_transient,
        "force_sustained": args.force_sustained,
        "group": args.group,
        "anchor": args.anchor,
        "normalize_faders": args.normalize_faders,
        "execute": args.execute,
        "overwrite": args.overwrite,
        "output_folder": args.output_folder,
        "backup": args.backup,
        "report": args.report,
        "json": args.json,
        "_source_dir": source_dir,
    }
    config = merge_configs(config, cli_overrides)

    # --- HEADER PANEL ---
    rms_anchor_label = f"{args.rms_anchor}" + (f" P{args.rms_percentile:g}" if args.rms_anchor == "percentile" else "")
    mode_label = "EXECUTE" if args.execute else "DRY-RUN"
    output_label = "(overwrite)" if is_overwriting else f"{args.output_folder}/"
    console.print(Panel.fit(
        f"[bold]SessionPrep[/]\n"
        f"Mode: [cyan]{mode_label}[/]\n"
        f"Target: [cyan]{args.target_rms} dB RMS[/] | [cyan]{args.target_peak} dB Peak[/]\n"
        f"RMS Anchor: [cyan]{rms_anchor_label}[/] | Window: [cyan]{args.window} ms[/]\n"
        f"Output: [green]{output_label}[/]",
        title="Configuration"
    ))

    # --- BUILD PIPELINE ---
    event_bus = EventBus()
    pipeline = Pipeline(
        detectors=default_detectors(),
        audio_processors=default_processors(),
        config=config,
        event_bus=event_bus,
    )

    # --- LOAD SESSION ---
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        # Count files first for the progress bar
        wav_files = sorted(
            [f for f in os.listdir(source_dir) if f.lower().endswith(AUDIO_EXTENSIONS)],
            key=protools_sort_key,
        )
        if not wav_files:
            console.print(f"[red]No audio files found in {source_dir}[/]")
            return

        task_id = progress.add_task("[cyan]Loading & analyzing tracks...", total=len(wav_files))

        # Wire up progress callback
        def on_track_analyze_complete(**data):
            progress.advance(task_id)
        event_bus.subscribe("track.analyze_complete", on_track_analyze_complete)

        try:
            session = load_session(source_dir, config, event_bus=event_bus)
        except ValueError as e:
            console.print(f"[bold red]Error:[/] {e}")
            return

        # --- ANALYZE ---
        session = pipeline.analyze(session)

        event_bus.unsubscribe("track.analyze_complete", on_track_analyze_complete)

    # --- PLAN (compute gains, groups, fader offsets) ---
    session = pipeline.plan(session)

    # --- SORT by Pro Tools order ---
    session.tracks.sort(key=lambda t: protools_sort_key(t.filename))

    # --- PRINT GROUP ASSIGNMENTS (if any) ---
    if session.groups:
        grouped: dict[str, list] = {}
        for t in session.tracks:
            if t.group is not None:
                grouped.setdefault(t.group, []).append(t)

        console.print("")
        group_table = Table(box=box.ROUNDED, title="Gain-Linked Groups", title_justify="left")
        group_table.add_column("Group", style="bold cyan")
        group_table.add_column("Gain", justify="right", style="bold green")
        group_table.add_column("Members", style="dim")

        def _get_primary_pr(track):
            if track.processor_results:
                return next(iter(track.processor_results.values()))
            return None

        for gname in sorted(grouped.keys()):
            members = grouped[gname]
            pr = _get_primary_pr(members[0])
            gain_str = f"{pr.gain_db:+.1f} dB" if pr else "—"
            member_list = ", ".join(m.filename for m in members)
            group_table.add_row(gname, gain_str, member_list)

        console.print(group_table)

        for w in session.warnings:
            if str(w).startswith("Group overlap:"):
                console.print(f"  [yellow]⚠ {w}[/]")

        console.print("")

    # --- BUILD DIAGNOSTIC SUMMARY ---
    diagnostic_summary = build_diagnostic_summary(session)

    # --- DRY-RUN: print diagnostics and exit ---
    if not args.execute:
        print_diagnostic_summary(diagnostic_summary)
        return

    # --- EXECUTE: write processed files ---
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task_id = progress.add_task("[cyan]Processing tracks...", total=len(session.tracks))

        def on_track_write_complete(**data):
            progress.advance(task_id)
        event_bus.subscribe("track.write_complete", on_track_write_complete)

        session = pipeline.execute(
            session,
            output_dir=output_dir,
            backup_dir=backup_dir,
            is_overwriting=is_overwriting,
        )

        event_bus.unsubscribe("track.write_complete", on_track_write_complete)

    # --- PRINT DIAGNOSTICS ---
    print_diagnostic_summary(diagnostic_summary)

    # --- DISPLAY FADER TABLE ---
    table = Table(box=box.ROUNDED, title="Fader Offsets")
    table.add_column("Track", style="cyan", max_width=30)
    table.add_column("Format", style="dim")
    table.add_column("Type", justify="center")
    table.add_column("Gain", justify="right")
    table.add_column("Fader", justify="right", style="bold green")
    table.add_column("Status", justify="right")

    def _get_primary_pr(track):
        if track.processor_results:
            return next(iter(track.processor_results.values()))
        return None

    for t in session.tracks:
        if t.status != "OK":
            table.add_row(t.filename, "Error", "\u2014", "\u2014", "\u2014", "[red]ERR[/]")
            continue

        fmt_str = f"{t.samplerate/1000:.0f}k/{t.bitdepth}"
        pr = _get_primary_pr(t)

        if pr and pr.classification == "Silent":
            table.add_row(t.filename, fmt_str, "Silent", "0.0 dB", "0.0 dB", "[yellow]SILENT[/]")
            continue

        classification = pr.classification if pr else "Unknown"
        gain_db = pr.gain_db if pr else 0.0
        fader_offset = pr.data.get("fader_offset", 0.0) if pr else 0.0
        is_clipped = False
        clip_r = t.detector_results.get("clipping")
        if clip_r:
            is_clipped = bool(clip_r.data.get("is_clipped"))

        type_color = "magenta" if "Transient" in classification else "cyan"
        status_str = "[red]CLIP[/]" if is_clipped else "[green]OK[/]"

        table.add_row(
            t.filename,
            fmt_str,
            f"[{type_color}]{classification}[/]",
            f"{gain_db:+.1f} dB",
            f"{fader_offset:+.1f} dB",
            status_str,
        )

    console.print(table)

    # --- BUILD WARNINGS & SAVE REPORTS ---
    warnings = build_warnings(session, config)
    diagnostic_summary_text = render_diagnostic_summary_text(diagnostic_summary)

    report_path = os.path.join(output_dir, args.report)
    generate_report(session, config, report_path, warnings, diagnostic_summary_text=diagnostic_summary_text)

    json_path = os.path.join(output_dir, args.json)
    save_json(session, config, json_path, warnings)

    console.print(f"\n[dim]Report saved to: {report_path}[/]")


if __name__ == "__main__":
    process_files()
