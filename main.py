from core.runtime_guard import preload_frozen_runtime

# Import the frozen runtime before the GUI: Windows temp cleaners can delete the
# extracted files while the pet runs (see core/runtime_guard.py).
preload_frozen_runtime()

from app.main_window import run

if __name__ == "__main__": run()
