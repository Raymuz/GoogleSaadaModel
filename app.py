# GoogleSaada Hugging Face entry point — Stage 14
"""
Preserves the existing authenticated five-tab Gradio app.
Forces the Hugging Face application bind settings.
"""

from pathlib import Path
import ast
import os
import runpy
import sys

ROOT = Path(__file__).resolve().parent

STAGING = ROOT / "app_private_googlesaada_auth_staging.py"
GATE = ROOT / "backend/google_saada_authenticated_gate.py"

def main():

    tree = ast.parse(
        GATE.read_text(encoding="utf-8")
    )

    values = []

    for node in tree.body:

        if isinstance(node, ast.Assign):
            if any(
                isinstance(t, ast.Name)
                and t.id == "_LIVE_RELEASE_APPROVED"
                for t in node.targets
            ):
                values.append(node.value)

        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "_LIVE_RELEASE_APPROVED"
            ):
                values.append(node.value)

    if not (
        len(values) == 1
        and isinstance(values[0], ast.Constant)
        and values[0].value is False
    ):
        raise RuntimeError(
            "GoogleSaada paid Agent release lock not verified"
        )

    import gradio as gr

    os.chdir(ROOT)

    for folder in (str(ROOT), str(ROOT / "backend")):
        if folder not in sys.path:
            sys.path.insert(0, folder)

    os.environ["GRADIO_SERVER_NAME"] = "0.0.0.0"

    port = int(os.environ.get("PORT", "7860"))

    os.environ["GRADIO_SERVER_PORT"] = str(port)

    os.environ.setdefault(
        "GRADIO_ANALYTICS_ENABLED", "False"
    )

    original_launch = gr.Blocks.launch

    def hf_launch(self, *args, **kwargs):

        kwargs["server_name"] = "0.0.0.0"
        kwargs["server_port"] = port
        kwargs["share"] = False
        kwargs["inbrowser"] = False

        return original_launch(
            self, *args, **kwargs
        )

    gr.Blocks.launch = hf_launch

    try:
        runpy.run_path(
            str(STAGING),
            run_name="__main__"
        )
    finally:
        gr.Blocks.launch = original_launch

if __name__ == "__main__":
    main()
