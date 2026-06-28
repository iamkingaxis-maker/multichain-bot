"""Guard: the live chart-inference runtime must NOT import torch.

AST-scans the runtime modules and asserts none import torch directly, nor
indirectly via models.chart_autoencoder / models.chart_cnn (whose module
tops import torch). This protects the ~1GB / ~$10/mo Railway saving from
silent regressions.
"""
import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_RUNTIME_FILES = [
    "core/chart_cluster_inference.py",
    "core/chart_cnn_inference.py",
    "core/np_nn.py",
    "core/chart_encoder_np.py",
    "core/chart_cnn_np.py",
]

# Importing these pulls torch at their module top.
_TORCH_PULLERS = {"models.chart_autoencoder", "models.chart_cnn"}


def _imports(path):
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


def test_runtime_does_not_import_torch():
    offenders = {}
    for rel in _RUNTIME_FILES:
        path = os.path.join(_ROOT, rel)
        mods = _imports(path)
        bad = [m for m in mods
               if m == "torch" or m.startswith("torch.") or m in _TORCH_PULLERS]
        if bad:
            offenders[rel] = bad
    assert not offenders, f"runtime modules import torch (directly/indirectly): {offenders}"
