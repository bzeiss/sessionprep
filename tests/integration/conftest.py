import pytest

try:
    from ptsl import PTSL_pb2 as pt
    from ptsl import Engine
except ImportError:
    pt = None
    Engine = None

from sessionpreplib.daw_processors import ptsl_helpers

def pytest_configure(config):
    config.addinivalue_line("markers", "ptsl_live: mark test to require a live Pro Tools connection")

@pytest.fixture(scope="session")
def live_engine():
    """Provides a connected PTSL Engine to a running Pro Tools instance.
    
    Skips the entire test module if Pro Tools cannot be reached.
    """
    if not pt or not Engine:
        pytest.skip("py-ptsl not installed")
        
    try:
        # Attempt to connect to default gRPC port
        engine = Engine(
            company_name="SessionPrep tests",
            application_name="pytest",
            address="localhost:31416"
        )
        
        # Verify connectivity by getting session name
        if not ptsl_helpers.is_session_open(engine):
            pytest.skip("Pro Tools is running, but no session is open.")
            
        yield engine
        
    except Exception as e:
        pytest.skip(f"Could not connect to live Pro Tools instance: {e}")
        
    finally:
        try:
            # Clean up connection
            engine.close()
        except Exception:
            pass
