"""
Engine loader — executes Cell 0 of the quant notebook and exposes its functions.

Usage:
    from engine_loader import engine
    cfg = engine.Config()
    pack = engine.prepare_inputs(cfg)
"""

import json
import os
import sys
import types
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _THIS_DIR.parent  # 0316-/

_ENGINE_MODULE = None


def _load_engine(notebook_path: str = None) -> types.ModuleType:
    global _ENGINE_MODULE
    if _ENGINE_MODULE is not None:
        return _ENGINE_MODULE

    if notebook_path is None:
        notebook_path = str(_PROJECT_DIR / "0315 windows이사.ipynb")

    if not os.path.exists(notebook_path):
        raise FileNotFoundError(f"Notebook not found: {notebook_path}")

    with open(notebook_path, "r", encoding="utf-8") as f:
        nb = json.load(f)

    cell0_source = "".join(nb["cells"][0]["source"])

    mod = types.ModuleType("quant_engine")
    mod.__file__ = notebook_path
    mod.__package__ = None
    sys.modules["quant_engine"] = mod

    old_cwd = os.getcwd()
    os.chdir(str(_PROJECT_DIR))
    try:
        exec(compile(cell0_source, notebook_path, "exec"), mod.__dict__)
    finally:
        os.chdir(old_cwd)
    _ENGINE_MODULE = mod
    return mod


engine = _load_engine()
