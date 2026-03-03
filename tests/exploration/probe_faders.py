"""
Diagnostic script for interacting with Pro Tools faders manually.

Run via: uv run python tests/exploration/probe_faders.py
"""
import time
import sys

try:
    from ptsl import PTSL_pb2 as pt
    from ptsl import Engine
except ImportError:
    print("Error: py-ptsl not installed.")
    sys.exit(1)
    
from sessionpreplib.daw_processors import ptsl_helpers

def main():
    print("Connecting to Pro Tools Engine...")
    try:
        engine = Engine(
            company_name="SessionPrep Diagnostic",
            application_name="probe_faders",
            address="localhost:31416"
        )
    except Exception as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)
        
    try:
        session_name = engine.session_name()
        if not session_name:
            print("No active Pro Tools session open. Exiting.")
            sys.exit(1)
            
        print(f"Connected to session: '{session_name}'")
        
        # 1. Fetch tracks
        track_list = engine.track_list()
        
        audio_tracks = []
        for t in track_list:
            # Different py-ptsl versions return either "Audio", "TT_Audio", integer 2, or string "2"
            t_type = str(t.type)
            if t_type in ("Audio", "TT_Audio", str(pt.TT_Audio)):
                audio_tracks.append(t)
        
        if not audio_tracks:
            print("No Audio tracks found in the session.")
            sys.exit(0)
            
        print(f"Found {len(audio_tracks)} audio tracks.")
        
        # Test hypothesis: is the float value actual dB?
        # Set recognisable dB values and check the fader readout in Pro Tools
        # after hitting Play.  Pro Tools fader range is -inf to +12 dB.
        test_db_values = [
            +12.0,   # fader fully up
            +6.0,    # hot
            +3.0,
            0.0,     # unity gain
            -3.0,
            -6.0,    # common mix level
            -12.0,
            -18.0,   # SessionPrep sustained target
            -24.0,
            -36.0,
            -48.0,
            -60.0,
            -80.0,   # near silence
            # Rest of tracks: leave some extreme/edge values
        ]
        
        # Pad with 0.0 if we have more tracks than test values
        while len(test_db_values) < len(audio_tracks):
            test_db_values.append(0.0)
        
        job_id = None
        try:
            print("Creating Batch Job...")
            job_id = ptsl_helpers.create_batch_job(engine, "Probe Faders", "dB hypothesis test")
            print(f"Batch Job ID: {job_id}")
            print()
            print(f"  {'Track':<25} {'Value sent':>12}   {'Expected if dB':>16}")
            print(f"  {'-'*25} {'-'*12}   {'-'*16}")
            
            for i, track in enumerate(audio_tracks[:len(test_db_values)]):
                val = test_db_values[i]
                progress = int((i + 1) / len(audio_tracks) * 100)
                print(f"  {track.name:<25} {val:>+12.1f}   {'<-- check fader':>16}")
                
                try:
                    ptsl_helpers.set_track_volume_by_trackname(
                        engine, track.name, val,
                        batch_job_id=job_id, progress=progress)
                except Exception as e:
                    print(f"     [FAILED] {e}")
                    
            print()
            print("Hit PLAY in Pro Tools, then read the fader dB values.")
            print("If they match the 'Value sent' column, the value IS actual dB.")
            
        finally:
            if job_id:
                print("Completing Batch Job...")
                ptsl_helpers.complete_batch_job(engine, job_id)
        
    except Exception as e:
        print(f"Unexpected error during script execution: {e}")
        
    finally:
        print("Closing engine connection.")
        engine.close()

if __name__ == "__main__":
    main()

