import sys
from pathlib import Path

# Make the LC-Backend root importable so tests can `import app.*`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
