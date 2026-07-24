"""ARPAD — Chat mit Claude über die FIDE-Rating-Daten."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html, no_update

import data_arpad

dash.register_page(
    __name__,
    path="/arpad",
    name="ARPAD",
    title="ELO-Einsichten | ARPAD",
    order=4,
)

C_BG      = "#F5F5F5"
C_CARD    = "#FFFFFF"
C_BORDER  = "#E0E0E0"
C_TEXT    = "#333333"
C_MUTED   = "#888888"
C_NEG     = "#E88080"

CARD = {
    "backgroundColor": C_CARD,
    "border": f"1px solid {C_BORDER}",
    "borderRadius": "6px",
    "padding": "12px 16px",
    "marginBottom": "12px",
}

MAX_QUESTION_LEN = 2000  # einfacher Guard gegen Copy-Paste-Missbrauch


def _bubble(role: str, text: str):
    if role == "user":
        cls = "arpad-bubble arpad-bubble-user"
    elif role == "error":
        cls = "arpad-bubble arpad-bubble-error"
    else:
        cls = "arpad-bubble arpad-bubble-assistant"
    return html.Div(text, className=cls)


def _render_bubbles(history, error_text=None):
    if not history:
        bubbles = [html.Div(
            "Frag mich etwas zu den FIDE-Rating-Daten — z.B. \"Wie hat sich das Rating von "
            "Judit Polgár entwickelt?\"",
            style={"color": C_MUTED, "fontSize": "0.9rem", "padding": "8px"},
        )]
    else:
        bubbles = [_bubble(m["role"], m["content"]) for m in history]
    if error_text:
        bubbles.append(_bubble("error", error_text))
    return bubbles


layout = dbc.Container(
    fluid=True,
    style={"backgroundColor": C_BG, "minHeight": "100vh", "padding": "20px"},
    children=[
        dcc.Store(id="arpad-history-store", data=[]),
        html.H4("ARPAD — Frag die Daten", style={"marginBottom": "16px", "color": C_TEXT}),

        html.Div(
            id="arpad-chat-window",
            style={
                **CARD,
                "minHeight": "50vh", "maxHeight": "65vh", "overflowY": "auto",
                "display": "flex", "flexDirection": "column", "gap": "10px",
            },
            children=_render_bubbles([]),
        ),

        html.Div(
            style={**CARD, "display": "flex", "gap": "8px", "alignItems": "center"},
            children=[
                dcc.Input(
                    id="arpad-input",
                    type="text",
                    placeholder="Frage zu den Rating-Daten stellen…",
                    maxLength=MAX_QUESTION_LEN,
                    debounce=False,
                    style={
                        "flex": "1", "fontSize": "0.9rem", "padding": "10px 12px",
                        "border": f"1px solid {C_BORDER}", "borderRadius": "6px",
                    },
                ),
                dbc.Button("Senden", id="arpad-send-btn", color="primary"),
            ],
        ),
    ],
)


@callback(
    Output("arpad-history-store", "data"),
    Output("arpad-chat-window", "children"),
    Output("arpad-input", "value"),
    Input("arpad-send-btn", "n_clicks"),
    Input("arpad-input", "n_submit"),
    State("arpad-input", "value"),
    State("arpad-history-store", "data"),
    prevent_initial_call=True,
)
def send_message(n_clicks, n_submit, question, history):
    history = history or []
    question = (question or "").strip()
    if not question:
        return no_update, no_update, no_update

    try:
        answer, new_history = data_arpad.answer_question(question, history)
        return new_history, _render_bubbles(new_history), ""
    except data_arpad.ArpadError as e:
        return history, _render_bubbles(history, error_text=str(e)), ""
    except Exception as e:
        return history, _render_bubbles(history, error_text=f"Unerwarteter Fehler: {e}"), ""
