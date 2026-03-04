import pytest
import time
from sessionpreplib.daw_processors import ptsl_helpers

# Marks all tests in this file as requiring a live PT connection
pytestmark = pytest.mark.ptsl_live

def test_read_all_tracks_and_folders(live_engine, capsys):
    """
    Reads the track list from the live session and prints basic properties.
    """
    with capsys.disabled():
        print("\n--- Live Tracks ---")
    
    # TrackListInSession is a direct engine operation in py-ptsl
    # For ptsl_helpers, we can call the SDK method. 
    # Usually it returns a pt.TrackListInSessionResponseBody structure
    try:
        track_list = live_engine.track_list()
        
        with capsys.disabled():
            for t in track_list:
                print(f"Track: '{t.name}' | ID: {t.id} | Type: {t.type} | Folder?: {t.is_folder}")
        
        assert len(track_list) > 0, "Session must contain at least one track for this test."
        
    except Exception as e:
        pytest.fail(f"Failed to read track list: {e}")


def test_set_faders_for_existing_audio_tracks(live_engine, capsys):
    """
    Finds all audio tracks and attempts to set a fader value via set_track_volume.
    
    Note: PTSL currently fails to read back fader values reliably
    (CId_GetTrackControlBreakpoints is Unsupported in 2025.10).
    Thus, this test only verifies that the command is accepted (Completed)
    and doesn't throw a RuntimeError. Visually monitor Pro Tools to confirm! 
    """
    track_list = live_engine.track_list()
    from ptsl import PTSL_pb2 as pt
    
    # Filter to Audio tracks
    audio_tracks = []
    for t in track_list:
        t_type = str(t.type)
        if t_type in ("Audio", "TT_Audio", str(pt.TT_Audio)):
            audio_tracks.append(t)
    
    if not audio_tracks:
        pytest.skip("No audio tracks found in the live session to test faders on.")
        
    with capsys.disabled():
        print(f"\n--- Setting fader on {len(audio_tracks)} audio tracks ---")
        
    for i, track in enumerate(audio_tracks):
        # Alternate fader values slightly so we can watch them jump
        target_db = -6.0 - (0.5 * i)
        
        with capsys.disabled():
            print(f"[{i+1}/{len(audio_tracks)}] Setting {track.name} (ID: {track.id}) to {target_db} dB")
        
        # This will raise a RuntimeError if PT rejects the fader command.
        ptsl_helpers.set_track_volume(live_engine, track.id, target_db)
    
    # Give the Mix Engine a tiny bit of time to digest the commands 
    time.sleep(1.0)
    
    with capsys.disabled():
        print("Done. Please verify in the PT Mix window.")
