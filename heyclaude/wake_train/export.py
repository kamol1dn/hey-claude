"""Write a trained head as an ONNX model in openWakeWord's format.

openwakeword.Model loads any .onnx whose first input is (batch, 16, 96)
float32 and whose first output is (batch, 1); it reads input.shape[1] (16
frames) and output.shape[1] (1 class) and names the model after the file, so
`hey_claude.onnx` shows up as "hey claude". listen.py loads the result with
no changes.

Opset 17 (LayerNormalization is a single op there); onnxruntime >= 1.14.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .head import EPS, HeadParams

OPSET = 17


def to_onnx(p: HeadParams, meta: dict | None = None):
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    d_in, hidden = p.W1.shape
    frames = 16
    inits = [numpy_helper.from_array(np.asarray(v, np.float32), k) for k, v in p.arrays().items()]
    inits.append(numpy_helper.from_array(np.array([-1, d_in], np.int64), "flat_shape"))
    nodes = [
        helper.make_node("Reshape", ["x", "flat_shape"], ["flat"]),
        helper.make_node("Gemm", ["flat", "W1", "b1"], ["z1"]),
        helper.make_node("LayerNormalization", ["z1", "g1", "be1"], ["a1"], axis=-1, epsilon=EPS),
        helper.make_node("Relu", ["a1"], ["r1"]),
        helper.make_node("Gemm", ["r1", "W2", "b2"], ["z2"]),
        helper.make_node("LayerNormalization", ["z2", "g2", "be2"], ["a2"], axis=-1, epsilon=EPS),
        helper.make_node("Relu", ["a2"], ["r2"]),
        helper.make_node("Gemm", ["r2", "W3", "b3"], ["logit"]),
        helper.make_node("Sigmoid", ["logit"], ["score"]),
    ]
    graph = helper.make_graph(
        nodes, "wake_head",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["batch", frames, d_in // frames])],
        [helper.make_tensor_value_info("score", TensorProto.FLOAT, ["batch", 1])],
        initializer=inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET)],
                              producer_name="hey-claude wake_train")
    model.ir_version = 8                      # readable by onnxruntime 1.14+
    for k, v in {**p.meta, **(meta or {})}.items():
        e = model.metadata_props.add()
        e.key, e.value = str(k), str(v)
    onnx.checker.check_model(model)
    return model


def export(p: HeadParams, path, meta: dict | None = None) -> Path:
    """Write the .onnx atomically (the app may be watching the path)."""
    import onnx
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    onnx.save(to_onnx(p, meta), str(tmp))
    tmp.replace(path)
    return path


def read_meta(path) -> dict:
    """metadata_props of an exported model (empty for other models)."""
    try:
        import onnxruntime as ort
        s = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        return dict(s.get_modelmeta().custom_metadata_map)
    except Exception:
        return {}
