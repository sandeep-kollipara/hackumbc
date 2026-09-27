"""Dash search interface with standalone Panel result cards; one local web server."""
from html import escape
import json
import logging
from pathlib import Path

from dash import Dash, Input, Output, State, dcc, html
from flask import Response

from RAG_delivery.object_search import ObjectSearch, SearchError, database_status, load_settings
from result_view import render_results

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent


def read_status(config):
    try:
        status = database_status(config)
        options = [{"label": "All users", "value": "null"}]
        options += [{"label": user or "Unknown user", "value": json.dumps(user)} for user in status["users"]]
        return options, f"{status['records']:,} indexed sightings", "Ready to search your recorded objects."
    except SearchError as exc:
        return [{"label": "All users", "value": "null"}], "Database unavailable", str(exc)
    except Exception:
        logger.exception("Could not open database")
        return [{"label": "All users", "value": "null"}], "Database unavailable", "Could not open Chroma. Check the terminal logs and data directory."


def search_response(config, query, username_value, limit, threshold, service=None):
    if not query or not query.strip():
        return empty_document("Enter an object question to start searching."), "Enter a question first."
    try:
        username = json.loads(username_value or "null")
        if username is not None and not isinstance(username, str):
            raise ValueError("Invalid user filter")
        payload = (service or ObjectSearch(config)).search(query, username, int(limit), float(threshold))
        document = render_results(payload, config)
        found = sum(len(group["results"]) for group in payload.get("groups", []))
        return document, f"{found} latest sightings shown · {payload.get('scanned', 0)} observations searched"
    except SearchError as exc:
        return empty_document(str(exc)), "Search could not finish."
    except Exception:
        logger.exception("Object search failed")
        return empty_document("Search failed. Check OpenAI credentials, model access, and your network connection. Details are in the terminal."), "Search failed."


def empty_document(message):
    return '<!doctype html><html><head><meta charset="utf-8"></head><body style="font:16px system-ui;color:#52645c;background:#f5f7f3;padding:32px">' + escape(message) + '</body></html>'


def create_app(config=None, service=None):
    config = config or load_settings()
    options, count, status = read_status(config)
    app = Dash(__name__, assets_folder=str(ROOT / "assets"), title="Last Seen · Object Search")
    app.layout = html.Div([
        html.Header([
            html.Div([html.Span("◉", className="brand-mark"), html.Span("LAST SEEN", className="brand")]),
            html.Span("Your visual memory", className="header-note"),
        ], className="topbar"),
        html.Main([
            html.Aside([
                html.Div("SEARCH LIBRARY", className="eyebrow"),
                html.H2("Find what you\u2019re looking for."),
                html.P("Look back through your camera observations to find the latest sighting of an object.", className="muted"),
                html.Div(count, id="record-count", className="count-pill"),
                html.Label("Captured by", htmlFor="username"),
                dcc.Dropdown(id="username", options=options, value="null", clearable=False),
                html.Label("Instances per object", htmlFor="limit"),
                dcc.Dropdown(id="limit", options=[{"label": str(x), "value": x} for x in [1, 3, 5, 10]], value=3, clearable=False),
                html.Details([
                    html.Summary("Match settings"),
                    html.Label("Semantic match tolerance", htmlFor="threshold"),
                    dcc.Slider(id="threshold", min=0, max=1, step=0.05, value=0.35,
                               marks={0: "Strict", 0.5: "0.5", 1: "Broad"}),
                    html.P("Lower values require closer matches. Increase to include related object names.", className="small muted"),
                ]),
                html.Button("Refresh library", id="refresh", className="secondary"),
                html.P("Sightings are recorded observations, not live locations. Similar-looking objects may share a group.", className="small footnote"),
            ], className="sidebar"),
            html.Section([
                html.Div("OBJECT SEARCH", className="eyebrow"),
                html.H1("Where did you last see it?"),
                html.P("Ask about one object or several. Each result includes its last recorded location and surroundings.", className="intro"),
                html.Div([
                    html.Label("Objects to find", htmlFor="query", className="sr-only"),
                    dcc.Input(id="query", type="text", placeholder="Where are my keys and headphones?", maxLength=2000,
                              autoComplete="off", className="query-input"),
                    html.Button("Find objects →", id="search", className="primary"),
                ], className="search-box"),
                html.P('Try “Show the last seen cups” or “Where is my backpack?” Each question is independent.', className="small muted"),
                html.Div(status, id="library-status", role="status", className="small muted"),
                html.Div("", id="search-status", role="status", className="search-status"),
                dcc.Loading(html.Iframe(id="results", title="Latest object sightings", className="results-frame",
                                       srcDoc=empty_document("Your matching objects will appear here."),
                                       sandbox="allow-scripts"), type="circle", color="#286747"),
            ], className="content"),
        ], className="workspace"),
    ], className="app")

    @app.callback(Output("results", "srcDoc"), Output("search-status", "children"),
                  Input("search", "n_clicks"), Input("query", "n_submit"),
                  State("query", "value"), State("username", "value"),
                  State("limit", "value"), State("threshold", "value"),
                  prevent_initial_call=True, running=[(Output("search", "disabled"), True, False)])
    def search(_clicks, _submits, query, username, limit, threshold):
        return search_response(config, query, username, limit, threshold, service)

    @app.callback(Output("username", "options"), Output("record-count", "children"), Output("library-status", "children"),
                  Input("refresh", "n_clicks"), prevent_initial_call=True)
    def refresh(_clicks):
        return read_status(config)

    @app.server.get("/health")
    def health():
        return Response("ok", mimetype="text/plain")

    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Search latest object sightings using Dash and Panel")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    create_app().run(host="127.0.0.1", port=args.port, debug=False)
