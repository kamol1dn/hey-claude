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


def to_onnx(p, meta: dict | None = None):
    """One head, or an ensemble (a list of heads whose logits are averaged)."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    heads = list(p) if isinstance(p, (list, tuple)) else [p]
    d_in, hidden = heads[0].W1.shape
    frames = 16
    inits = [numpy_helper.from_array(np.array([-1, d_in], np.int64), "flat_shape")]
    nodes = [helper.make_node("Reshape", ["x", "flat_shape"], ["flat"])]
    outs = []
    for i, h in enumerate(heads):
        n = (lambda k, i=i: k if len(heads) == 1 else f"h{i}_{k}")  # noqa: E731
        inits += [numpy_helper.from_array(np.asarray(v, np.float32), n(k)) for k, v in h.arrays().items()]
        nodes += [
            helper.make_node("Gemm", ["flat", n("W1"), n("b1")], [n("z1")]),
            helper.make_node("LayerNormalization", [n("z1"), n("g1"), n("be1")], [n("a1")], axis=-1, epsilon=EPS),
            helper.make_node("Relu", [n("a1")], [n("r1")]),
            helper.make_node("Gemm", [n("r1"), n("W2"), n("b2")], [n("z2")]),
            helper.make_node("LayerNormalization", [n("z2"), n("g2"), n("be2")], [n("a2")], axis=-1, epsilon=EPS),
            helper.make_node("Relu", [n("a2")], [n("r2")]),
            helper.make_node("Gemm", [n("r2"), n("W3"), n("b3")], [n("logit")]),
        ]
        outs.append(n("logit"))
    if len(heads) == 1:
        nodes.append(helper.make_node("Sigmoid", ["logit"], ["score"]))
    else:
        inits.append(numpy_helper.from_array(np.array(1.0 / len(heads), np.float32), "inv_n"))
        nodes += [helper.make_node("Sum", outs, ["logit_sum"]),
                  helper.make_node("Mul", ["logit_sum", "inv_n"], ["logit"]),
                  helper.make_node("Sigmoid", ["logit"], ["score"])]
    p = heads[0]
    graph = helper.make_graph(
        nodes, "wake_head",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["batch", frames, d_in // frames])],
        [helper.make_tensor_value_info("score", TensorProto.FLOAT, ["batch", 1])],
        initializer=inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET)],
                              producer_name="hey-claude wake_train")
    model.ir_version = 8                      # readable by onnxruntime 1.14+
    for k, v in {**p.meta, "heads": len(heads), **(meta or {})}.items():
        e = model.metadata_props.add()
        e.key, e.value = str(k), str(v)
    onnx.checker.check_model(model)
    return model


def export(p, path, meta: dict | None = None) -> Path:
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
