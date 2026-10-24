"""从零训练部位分层模型的最小工具链。

只依赖 torch / numpy / Pillow，不引用 ``core``，方便在独立环境里训练。
训练产物通过 ``core/custom_model`` 的 ONNX 契约接回主流程。
"""
