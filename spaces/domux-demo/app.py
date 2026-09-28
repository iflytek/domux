"""Gradio demo for Domux: natural-language smart-home commands to structured slots."""

try:
    # Must be imported before torch on ZeroGPU Spaces; optional everywhere else.
    import spaces
except ImportError:
    spaces = None

import gradio as gr

from domux_backend import FIELDS, backend_from_env, run

EXAMPLES = [
    "Turn on the living room light",
    "Set bedroom AC to 22 degrees",
    "dim the spots in the living room",
    "set the curtain in the majlis on the ground floor to 65 percent",
    "turn on the ceiling light and open the curtain a bit in bedroom 1",
    "Turn off all lights in the Living Room on the Ground Floor, set the AC to Cool mode at "
    "24 degrees in the Guest Bedroom, and open the curtains halfway in the Dining Room.",
    "Turn on the Master Light in the Master Bedroom on the Second Floor, set brightness to 80%, "
    "color temperature to 4000K, color to Blue, and mode to Reading.",
    "Activate romantic mode",
]

DESCRIPTION = """\
# Domux · Smart-home command understanding

[Domux](https://github.com/iflytek/domux) turns a natural-language smart-home command into
pipe-delimited slots: `action|device|attribute|value|unit|room|floor`, one line per action,
`*` for fields that don't apply. It is fine-tuned from Gemma-4-E2B-it and trained on English
commands for lights, AC, curtains/blinds and scene modes.

[Model](https://huggingface.co/iFlytekOpenSource/Domux) ·
[Output spec](https://github.com/iflytek/domux/blob/main/docs/output-spec.md) ·
[Benchmark](https://github.com/iflytek/domux#-benchmark)
"""

backend = backend_from_env()
generate = backend.generate
if spaces is not None and backend.__class__.__name__ == "LocalBackend":
    generate = spaces.GPU(duration=30)(generate)


def parse(query: str):
    try:
        result = run(generate, query)
    except ValueError as exc:
        raise gr.Error(str(exc)) from exc
    status = (
        f"{'✅ Valid' if result.format_ok else '⚠️ Not 7-field'} format · "
        f"{len(result.rows)} action(s) · {result.latency_ms:.0f} ms"
    )
    return result.raw, result.rows, status


with gr.Blocks(title="Domux Demo") as demo:
    gr.Markdown(DESCRIPTION)
    with gr.Row():
        with gr.Column():
            query = gr.Textbox(
                label="Command",
                placeholder="e.g. Set the bedroom AC to 24 degrees and close the curtains",
                lines=3,
                max_length=1000,
            )
            submit = gr.Button("Parse", variant="primary")
            gr.Examples(examples=EXAMPLES, inputs=query)
        with gr.Column():
            status = gr.Markdown()
            slots = gr.Dataframe(headers=FIELDS, label="Slots", interactive=False, wrap=True)
            raw = gr.Code(label="Raw model output", language=None)
    gr.Markdown(
        f"Backend: `{backend.label}`. Latency shown here includes Space queueing and GPU "
        "allocation, so it is higher than the benchmark numbers."
    )

    submit.click(parse, inputs=query, outputs=[raw, slots, status])
    query.submit(parse, inputs=query, outputs=[raw, slots, status])


if __name__ == "__main__":
    demo.queue(max_size=32).launch()
